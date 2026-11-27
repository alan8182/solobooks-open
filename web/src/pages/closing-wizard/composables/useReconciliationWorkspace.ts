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
import { computed, h, ref, watch, type Ref } from 'vue'
import { useRouter } from 'vue-router'
import { useDialog, useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { previewEntryVoucher, updateEntry, fetchPriorPaymentCandidates, linkPriorPayment, type PriorPaymentCandidate } from '@/api/record'
import { applySalaryPayment, applyPayrollLiability } from '@/api/salary'
import { reverseEntry } from '@/api/accounting'
import { accountingErrorText } from '@/utils/errors'
import { formatMoney } from '@/utils/format'
import { settlementCancelMessage } from '@/utils/settlementType'
import { sortEntriesByDateDescending } from '@/utils/entrySort'
import { getRdExpenseTypes, getRdProjects } from '@/api/rd'
import type { RdExpenseType, RdExpenseTypeOption, RdProject, VoucherPreview } from '@/api/types'
import {
  buildSuggestedMatchPair,
  voucherLinesSummary,
  getStatementMatchedEntryIds,
  getMatchedEntryIdSets,
  isReconciledEntry,
  isNonBankSettledEntry,
  isUnreimbursedPersonalAdvanceEntry,
  computeBossLedgerSummary,
  type AdvanceBalancesSnapshot,
  isReversalLikeEntry,
  canResetEntryDisposition,
  isLiveInvoiceEntry,
  invoiceDedupKey,
  isPreferredInvoiceEntry,
  invoiceRowStatus,
  invoiceNeedsAttention,
  DISPOSITION_EXCLUDED_CATEGORIES,
  RD_EXPENSE_CATEGORY,
  dispositionCategoryGroups,
  dispositionCategoryOf,
  hasEffectiveRdProjects,
  isEffectiveRdProject,
  statementSellerAffinity,
  parseBankStatementDisplay,
  classifyMultiMatchCandidate,
  collectSuggestedMatchedEntryIds,
  isFallbackInvoiceCategory,
} from '@/utils/closingWizard'
import { getEntryNaturePresentation } from '@/utils/entryNature'
import { accountForCategory, getAccountMappingItems, getAmbiguityGroup } from '@/utils/accounting'
import { useBackfillConfirm, isBackfillCancelled } from '@/composables/backfillConfirm'
import type { useEntryStore } from '@/stores/entry'

/** 平台净额结算凭证类型：微信 / 支付宝 / 历史其他货币资金。 */
const SETTLEMENT_RECONCILIATION_TYPES = new Set([
  'wechat_settlement',
  'alipay_settlement',
  'platform_settlement',
])

export function useReconciliationWorkspace(options: {
  month: Ref<string>
  currentStep: Ref<number>
  entryPeriod: (e: any) => string
  entryStore: ReturnType<typeof useEntryStore>
  backfillConfirm: ReturnType<typeof useBackfillConfirm>
  openVoucherModal: (entryId: string) => Promise<void>
  scrollIntoBank?: (statementId: string) => void
  /** 跨域共享：工资名册（员工垫付选项）与批量过账弹层当前凭证（改凭证弹窗） */
  employees: Ref<any[]>
  currentVoucherEntry: Ref<any>
  reloadAll: () => Promise<void>
  apiErrorMessage: (err: any, fallback: string) => string
}) {
  const { month: targetMonth, currentStep, entryPeriod, entryStore, backfillConfirm, openVoucherModal, employees, currentVoucherEntry, reloadAll, apiErrorMessage } = options
  const router = useRouter()
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
// 真正持久化的配对（流水 matched_entry_id 已落库）。建议对（persisted=false，
// 待用户点确认配对）不得混进"已认领并完成"列表——否则用户点撤销只是本地
// 忽略，刷新后 ai-suggest 重新建议，看起来像"撤销无效又回来了"。
const persistedMatchedPairs = computed(() => matchedPairs.value.filter((p: any) => p.persisted))
const aiSuggestions = ref<any[]>([])
const aiSuggestionsLoading = ref(false)
const reconciliationHealth = ref<any | null>(null)
const reconciliationHealthLoading = ref(false)
// 前期已付款候选（先付款后收票）：发票挂账草稿在当月公户找不到扣款时，
// 跨期搜索「金额一致 + 日期不晚于发票日 + 公户已入账」的凭证供一键挂接，
// 挂接后本月不再生成重复费用（DeepSeek 等充值即开票的常态场景）。
const priorPaidCandidates = ref<Record<string, PriorPaymentCandidate[]>>({})
const priorPaidCandidatesLoading = ref(false)
const priorPaidLinkingId = ref<string | null>(null)
const expandedPriorPaidEntryId = ref<string | null>(null)


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

  // 月度发票：只保留活凭证（冲销凭证与被冲销原凭证退出），并按发票号/指纹去重，
  // 避免一次“冲销重记”把同一张发票显示成两行。
  const { linkedEntryIds, confirmedEntryIds } = getMatchedEntryIdSets(rawStatements.value, entryStore.activeEntries)
  const rawInvoices = entryStore.activeEntries.filter((e) => {
    const isInvoice = (e as any).source === 'invoice_import' || !!(e as any).invoiceNumber
    const isInMonth = entryPeriod(e) === targetMonth.value
    return isInvoice && isInMonth && isLiveInvoiceEntry(e as any)
  })
  const byKey = new Map<string, any>()
  for (const entry of rawInvoices) {
    const key = invoiceDedupKey(entry as any)
    const current = byKey.get(key)
    if (!current || isPreferredInvoiceEntry(entry as any, current as any)) byKey.set(key, entry)
  }
  invoicesList.value = [...byKey.values()].map(e => {
    const row: any = {
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
      invoiceNumber: (e as any).invoiceNumber || '',
      // billingStatus='reconciled' 由后端在报销垫付核销时写入（流水指向 reim- 账目，原发票账目同步结清），视为已对上
      status: confirmedEntryIds.has(e.id) || e.billingStatus === 'reconciled' ? 'matched' : linkedEntryIds.has(e.id) ? 'pending_confirmation' : 'unmatched',
    }
    row.rowStatus = invoiceRowStatus(row)
    return row
  })
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
  const controller = new AbortController()
  suggestionsController = controller
  aiSuggestionsLoading.value = true

  try {
    const res: any = await apiClient.post('/api/bank/ai-suggest', { month: targetMonth.value }, {
      timeout: 60000,
      signal: controller.signal
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
  } finally {
    if (suggestionsController === controller) {
      aiSuggestionsLoading.value = false
    }
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

const advanceBalances = ref<AdvanceBalancesSnapshot | null>(null)

async function loadAdvanceBalances() {
  const reqMonth = targetMonth.value
  try {
    const res: any = await apiClient.get('/api/ledger/advance-balances', {
      params: { month: reqMonth },
    })
    if (targetMonth.value !== reqMonth) return
    advanceBalances.value = res && Array.isArray(res.items) && res.totals != null ? res : null
  } catch (err) {
    console.warn('[loadAdvanceBalances] failed, falling back to local entries:', err)
    if (targetMonth.value === reqMonth) advanceBalances.value = null
  }
}
// 撤销认可入账创建的凭证（source=bank_import）：草稿删除、已过账红冲。
// 发票/工资等其他来源的凭证不动。返回失败的凭证标题列表。
async function removeBankCreatedEntries(bankCreated: any[]): Promise<string[]> {
  const failed: string[] = []
  for (const e of bankCreated) {
    try {
      if ((e.voucherStatus || e.status) === 'posted') {
        await reverseEntry(String(e.id), '对账撤销入账')
      } else {
        await apiClient.delete(`/api/record/entries/${e.id}`)
      }
    } catch (err: any) {
      // 快照状态可能过期：DELETE 撞"已过账不可删"（409）时回退红冲，
      // 不要把这种可自动恢复的失败抛给用户
      const code = String(err?.response?.data?.detail?.code || err?.response?.data?.code || '')
      if (err?.response?.status === 409 || code.includes('POSTED')) {
        try {
          await reverseEntry(String(e.id), '对账撤销入账')
          continue
        } catch {
          /* 红冲也失败则计入 failed */
        }
      }
      failed.push(e.title || String(e.id))
    }
  }
  return failed
}

// match 型待处理行上"可撤销原入账"的凭证：建议指向的账目里属于认可入账产物的
// （撤销后行回到全新待处理，换分类/详细记账全可用；否则只能确认配对或无视建议）
function matchUndoableEntries(stmt: any): any[] {
  const sug = getAiSuggestion(stmt.id)
  if (!sug || sug.type !== 'match') return []
  const ids = (sug.matchedEntryIds || [sug.matchedEntryId].filter(Boolean)).map(String)
  const byId = new Map(entryStore.activeEntries.map((e: any) => [String(e.id), e]))
  return ids
    .map((entryId: string) => byId.get(entryId))
    .filter((e: any) => e && String(e.source || '') === 'bank_import')
}

async function undoMatchEntry(stmt: any) {
  const undoable = matchUndoableEntries(stmt)
  if (!undoable.length) return
  dialog.warning({
    title: '撤销原入账',
    content: `账上已有「${undoable[0].title || '未命名'}」${undoable.length > 1 ? `等 ${undoable.length} 笔` : ''}（此前认可入账产生）。撤销入账后该流水回到全新待处理状态，可重新选择分类入账；保留则可点「确认配对」直接对上。`,
    positiveText: '撤销原入账，重新选择分类',
    negativeText: '保留在账上',
    onPositiveClick: async () => {
      // 顺序必须是先解链、再红冲：反了会留下「流水指向已冲销凭证」的悬空引用，
      // 流水不会回到待处理，后端也会拒绝冲销已关联的凭证（A3）。
      try {
        await apiClient.post('/api/bank/confirm-suggestion', {
          statement_id: stmt.id,
          type: 'match',
          matched_entry_id: '',
          matched_entry_ids: [],
        })
      } catch (err) {
        message.error(accountingErrorText(err))
        return
      }
      const failed = await removeBankCreatedEntries(undoable)
      if (failed.length) {
        message.warning(`部分凭证未能撤销（已过账请到账目页冲销）：${failed.join('、')}`)
      } else {
        message.success('原入账已撤销，该流水已回到待处理，可重新选择分类入账')
      }
      await reloadAll()
    },
  })
}

// Split / delete a pair
async function deletePair(id: string) {
  const pair = matchedPairs.value.find(p => p.id === id)
  if (!pair) return
  if (pair.status !== 'confirmed' && !pair.persisted) {
    matchedPairs.value = matchedPairs.value.filter(p => p.id !== id)
    message.info('已忽略该 AI 匹配建议')
    return
  }
  const originalIds: string[] = pair.matchedEntryIds || [pair.matchedEntryId].filter(Boolean)
  const entriesById = new Map((pair.entries || []).map((e: any) => [String(e.id), e]))
  const resolvedEntries = originalIds
    .map((entryId) => findEntryById(entryId) || entriesById.get(String(entryId)))
    .filter(Boolean)
  const settlementEntry: any = resolvedEntries.find((e: any) => String(e.id || '').startsWith('bank-settlement-'))
  const bankRelatedEntries: any[] = resolvedEntries.filter((e: any) => {
    const eid = String(e.id || '')
    return String(e.source || '') === 'bank_import'
      || eid.startsWith('bank-settlement-')
      || eid.startsWith('reim-')
  })

  if (bankRelatedEntries.length > 0) {
    const hasPosted = bankRelatedEntries.some((e: any) => isPostedEntry(e))
    dialog.warning({
      title: '确认撤销该笔流水入账',
      content: hasPosted
        ? '该笔流水关联的凭证此前已过账。撤销后将自动解绑流水并红字冲销对应入账/结算/报销凭证，流水将回到待处理列表供重新分类。'
        : '撤销后将解绑流水并删除对应的草稿入账凭证，流水将回到待处理列表供重新分类。',
      positiveText: '确认撤销',
      negativeText: '保留',
      onPositiveClick: async () => {
        try {
          await apiClient.post('/api/bank/confirm-suggestion', {
            statement_id: pair.statementId,
            type: 'match',
            matched_entry_id: '',
            matched_entry_ids: [],
            reversePostedBankEntries: true,
          })
          message.success('已撤销原入账，流水已回到待处理')
          await reloadAll()
        } catch (err) {
          message.error(accountingErrorText(err))
        }
      },
    })
    return
  }

  try {
    await apiClient.post('/api/bank/confirm-suggestion', {
      statement_id: pair.statementId,
      type: 'match',
      matched_entry_id: '',
      matched_entry_ids: [],
      reversePostedBankEntries: true,
    })
  } catch (err) {
    message.error(accountingErrorText(err))
    return
  }
  message.info(
    settlementEntry
      ? settlementCancelMessage(settlementEntry?.reconciliationType)
      : '对账关联已取消；原凭证仍保留在账上，可重新配对，或到凭证页删除',
  )
  await reloadAll()
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
    return invoicesList.value.filter((inv: any) => invoiceNeedsAttention(inv))
  } catch (err) {
    console.error('unmatchedInvoices computed error:', err)
    return []
  }
})

/** 本月活发票行（已去重）：表格展示用，包含已核销/已报销的历史状态。 */
const monthInvoiceRows = computed(() => invoicesList.value)

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
        settlement: entries.find((item) => SETTLEMENT_RECONCILIATION_TYPES.has(item.reconciliationType)),
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

    // 已关联流水但核销凭证仍是草稿：第二步口径是"已完成"，第三步银行核对却会拦
    // （核销凭证尚未过账 → 账面银行存款与银行期末余额对不上），在此提前亮出来
    const linkedUnposted = targetMonthStatements.filter((s) => {
      const matchedEntryIds = getStatementMatchedEntryIds(s)
      if (matchedEntryIds.length === 0) return false
      return matchedEntryIds.some((entryId) => {
        const entry = entriesById.get(entryId) as any
        return entry && (entry.status === 'draft' || entry.voucherStatus === 'draft')
      })
    })

    return { total, matched, unmatched, rate, unmatchedIncome, unmatchedExpense, linkedUnposted }
  } catch (err) {
    console.error('reconciliationStats computed error:', err)
    return { total: 0, matched: 0, unmatched: 0, rate: 0, unmatchedIncome: [], unmatchedExpense: [], linkedUnposted: [] }
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

/**
 * 已配对流水指向的有效凭证：确认前允许改分类/来源（未过账直接修改；已过账走红冲重记）。
 * 已冲销的凭证不可再改；合并多票走「调整多票勾选」。
 * 优先取 entryStore 里的完整记录（含 voucherStatus / voucherLines），
 * ai-suggest 带回的 matchedEntry 只是候选快照。
 */
function editableMatchedEntryOf(stmt: any) {
  const sug = getAiSuggestion(stmt?.id)
  if (!sug || sug.type !== 'match') return null
  if ((sug.matchedEntryIds?.length || 0) > 1) return null
  const id = String(sug.matchedEntryId || sug.matchedEntryIds?.[0] || '')
  if (!id) return null
  const entry = entryStore.activeEntries.find((item: any) => String(item.id) === id) || sug.matchedEntry
  if (!entry) return null
  const status = String(entry.voucherStatus || entry.status || '')
  // 已冲销的凭证不能再改；开账期内已过账凭证可通过红冲重记调整
  if (status === 'reversed') return null
  return entry
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
  { label: '咨询服务收入', value: 'income|咨询服务收入|5001001|主营业务收入-咨询服务' },
  { label: '技术服务收入', value: 'income|技术服务收入|5001002|主营业务收入-技术服务' },
  { label: '其他业务收入', value: 'income|其他业务收入|5051|其他业务收入' },
  { label: '营业外收入', value: 'income|营业外收入|5301|营业外收入' },
  { label: '利息收入', value: 'income|利息收入|5603005|财务费用-利息收入' },
  { label: '── 员工成本 ──', value: '__header_salary__', disabled: true },
  // 工资/社保流水走工资模块核销（应付职工薪酬），不直接记费用；与后端 account_mapper 对齐
  { label: '工资发放', value: 'expense|工资发放|221101|应付职工薪酬-工资' },
  { label: '社保公积金', value: 'expense|社保公积金|221102|应付职工薪酬-单位社保' },
  { label: '住房公积金', value: 'expense|社保公积金|221105|应付职工薪酬-住房公积金' },
  // 代扣个税不是公司费用：核销 2221012，不能落到 5801 所得税费用
  { label: '个税代缴', value: 'expense|个税代缴|2221012|应交税费-应交个人所得税' },
  { label: '── 费用支出 ──', value: '__header_expense__', disabled: true },
  { label: '差旅费', value: 'expense|差旅费|5602006|管理费用-差旅费' },
  { label: '办公费', value: 'expense|办公费|5602004|管理费用-办公费' },
  { label: '业务招待费', value: 'expense|业务招待费|5602002|管理费用-业务招待费' },
  { label: '研发支出-费用化支出', value: 'expense|研发支出-费用化支出|4301|研发支出-费用化支出' },
  { label: '信息技术服务费', value: 'expense|信息技术服务费|5602015|管理费用-信息技术服务费' },
  { label: '咨询费', value: 'expense|咨询费|5602011|管理费用-咨询费' },
  { label: '租金物业', value: 'expense|租金物业|5602013|管理费用-租赁费' },
  { label: '银行及金融手续费', value: 'expense|银行及金融手续费|5603002|财务费用-手续费' },
  { label: '水电通讯费', value: 'expense|水电通讯费|5602005|管理费用-水电费' },
  { label: '员工福利费', value: 'expense|员工福利费|5602014|管理费用-职工福利费' },
  { label: '推广费', value: 'expense|推广费|5601016|销售费用-业务宣传费' },
  { label: '运费及物流', value: 'expense|运费及物流|5601011|销售费用-运输费' },
  { label: '修理费', value: 'expense|修理费|5602003|管理费用-修理费' },
  { label: '开办费', value: 'expense|开办费|5602009|管理费用-开办费' },
  { label: '研究费用', value: 'expense|研究费用|5602010|管理费用-研究费用' },
  { label: '营业外支出', value: 'expense|营业外支出|5711|营业外支出' },
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
  { label: '── 销售与财务费用 ──', value: '__header_sales_finance__', disabled: true },
  { label: '广告费', value: 'expense|广告费|5601015|销售费用-广告费' },
  { label: '展览费', value: 'expense|展览费|5601009|销售费用-展览费' },
  { label: '商品维修费', value: 'expense|商品维修费|5601010|销售费用-商品维修费' },
  { label: '装卸费', value: 'expense|装卸费|5601012|销售费用-装卸费' },
  { label: '包装费', value: 'expense|包装费|5601013|销售费用-包装费' },
  { label: '保险费', value: 'expense|保险费|5601014|销售费用-保险费' },
  { label: '利息费用', value: 'expense|利息费用|5603001|财务费用-利息费用' },
  { label: '汇兑损益', value: 'expense|汇兑损益|5603003|财务费用-汇兑损益' },
  { label: '现金折扣', value: 'expense|现金折扣|5603004|财务费用-现金折扣' },
  { label: '── 营业外支出 ──', value: '__header_nonoperating_expense__', disabled: true },
  { label: '罚款支出', value: 'expense|罚款支出|5711011|营业外支出-罚款支出' },
  { label: '税收滞纳金', value: 'expense|税收滞纳金|5711010|营业外支出-税收滞纳金' },
  { label: '捐赠支出', value: 'expense|捐赠支出|5711003|营业外支出-捐赠支出' },
  { label: '赞助支出', value: 'expense|赞助支出|5711002|营业外支出-赞助支出' },
  { label: '非流动资产处置净损失', value: 'expense|非流动资产处置净损失|5711001|营业外支出-非流动资产处置净损失' },
  { label: '盘亏损失', value: 'expense|盘亏损失|5711004|营业外支出-盘亏损失' },
  { label: '坏账损失', value: 'expense|坏账损失|5711005|营业外支出-坏账损失' },
  { label: '存货毁损报废损失', value: 'expense|存货毁损报废损失|5711006|营业外支出-存货毁损报废损失' },
  { label: '长期债券投资损失', value: 'expense|长期债券投资损失|5711007|营业外支出-无法收回的长期债券投资损失' },
  { label: '长期股权投资损失', value: 'expense|长期股权投资损失|5711008|营业外支出-无法收回的长期股权投资损失' },
  { label: '自然灾害损失', value: 'expense|自然灾害损失|5711009|营业外支出-自然灾害等不可抗力因素造成的损失' },
  { label: '── 营业外收入 ──', value: '__header_nonoperating_income__', disabled: true },
  { label: '政府补助', value: 'income|政府补助|5301002|营业外收入-政府补助' },
  { label: '非流动资产处置净收益', value: 'income|非流动资产处置净收益|5301001|营业外收入-非流动资产处置净收益' },
  { label: '捐赠收益', value: 'income|捐赠收益|5301003|营业外收入-捐赠收益' },
  { label: '盘盈收益', value: 'income|盘盈收益|5301004|营业外收入-盘盈收益' },
]

const INTERNAL_DROPDOWN_EXCLUDED = new Set([
  '微信支付结算',
  '支付宝结算',
  '平台提现结算',
  '股东垫付报销',
  '股东取现',
])

// 下拉选项：accountCode/accountName 权威值从后端 /account-mapping 加载后刷新与合入，
// 静态清单保留既有分组排布及往来款收支特化，离线或测试只 mock 部分字段时不丢失基础选项。
const CATEGORY_OPTIONS = ref(CATEGORY_OPTIONS_RAW)

function refreshCategoryOptions() {
  const updated = CATEGORY_OPTIONS_RAW.map((opt: any) => {
    if (opt.disabled || !opt.value || opt.value.startsWith('__')) return opt
    const [direction, category] = opt.value.split('|')
    // 往来款收/支是前端特化，后端映射只给单一 code，跳过以免覆盖收=2241 的语义
    if (category === '往来款') return opt
    const acct = accountForCategory(category)
    if (!acct) return opt
    return { ...opt, value: `${direction}|${category}|${acct.code}|${acct.name}` }
  })

  const knownCategories = new Set(
    updated
      .filter((opt: any) => !opt.disabled && opt.value && !opt.value.startsWith('__'))
      .map((opt: any) => String(opt.value).split('|')[1]),
  )

  for (const item of getAccountMappingItems()) {
    if (item.isAlias || knownCategories.has(item.category) || INTERNAL_DROPDOWN_EXCLUDED.has(item.category)) {
      continue
    }
    const dir = item.direction || 'expense'
    const entry = {
      label: item.category,
      value: `${dir}|${item.category}|${item.accountCode}|${item.accountName}`,
    }
    const groupTitle = item.group ? `── ${item.group} ──` : ''
    const headerIndex = groupTitle
      ? updated.findIndex((opt: any) => opt.disabled && opt.label === groupTitle)
      : -1
    if (headerIndex >= 0) {
      let insertAt = headerIndex + 1
      while (insertAt < updated.length && !updated[insertAt].disabled) insertAt += 1
      updated.splice(insertAt, 0, entry)
    } else {
      if (groupTitle) {
        updated.push({ label: groupTitle, value: `__header_${item.group}__`, disabled: true })
      }
      updated.push(entry)
    }
    knownCategories.add(item.category)
  }

  CATEGORY_OPTIONS.value = updated
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
// 账目分类选项双栏渲染（名称居左、科目代码·名称居右）。必须挂在 render-label 上——
// render-option 返回自绘节点会整体替换 naive-ui 的选项元素，点击选中事件随之丢失
// （1b7f604 起"确认账目分类"下拉看得见点不着即此因）
function renderCategoryLabel(opt: any) {
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
/**
 * 与后端 _add_lines 的校验口径对齐：状态可用 **且** 项目周期覆盖目标月份。
 * 后端在记账日期早于 start_date 或晚于 end_date 时抛 RD_PROJECT_DATE_MISMATCH，
 * 这里提前按月收窄，避免用户在下拉里选到一个点确认才报错的项目。
 */
const effectiveRdProjects = computed(() => rdProjects.value
  .filter(project => isEffectiveRdProject(project, targetMonth.value)))
const rdProjectOptions = computed(() => rdProjects.value
  .filter(project => ['active', 'completed'].includes(project.status))
  .map(project => ({ label: `${project.code} · ${project.name}`, value: project.id })))
const effectiveRdProjectOptions = computed(() => effectiveRdProjects.value
  .map(project => ({ label: `${project.code} · ${project.name}`, value: project.id })))
const rdExpenseTypeOptions = computed(() => rdExpenseTypes.value
  .map(item => ({ label: item.label, value: item.value })))
/** 账号内有覆盖本月的有效研发项目时，结账第二步常驻「研发项目支出」选项。 */
const rdCategoryEnabled = computed(() => hasEffectiveRdProjects(rdProjects.value, targetMonth.value))

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
const manualAdvancedOpen = ref(false)
const manualLastAutoTitle = ref('')
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
  // 付款来源：未过账草稿在配对前就能改（老板/员工垫付、公司账户、还没付款），
  // 保存时与分类一并提交；已过账凭证不展示这组选项。
  payer: 'company_account',
  paymentStatus: '' as '' | 'unpaid',
})
const editVoucherSaving = ref(false)
// 改凭证弹窗的当前目标：过账弹层走 currentVoucherEntry，待确认卡片直接传 entry
const editVoucherTargetEntry = ref<any>(null)
const editVoucherBatchSiblings = ref<any[]>([])
const editVoucherApplyToSiblings = ref(false)
// 「修改单据」：业务说明 → AI 重推分类；科目预览与第二步同一口径
const editVoucherBusinessContext = ref('')
const editVoucherAiLoading = ref(false)
const editVoucherAiSuggestion = ref<{ category?: string; reason?: string } | null>(null)
const editVoucherPreview = ref<{
  loading: boolean
  preview?: VoucherPreview
  error?: string
  errorCode?: string
}>({ loading: false })

function defaultCategoryValue(stmt: any) {
  return stmt.amount >= 0
    ? 'income|主营业务收入|5001|主营业务收入'
    : 'expense|其他支出|5602999|管理费用-其他'
}

function cleanStatementTitle(stmt: any) {
  const desc = String(stmt.description || '')
  return desc.replace(/^\[.*?\]\s*/, '').replace(/^\(对方:\s*/, '').replace(/\)$/, '').trim() || desc || '银行流水记账'
}

function buildDefaultManualTitle(stmt: any, category?: string): string {
  const parsed = parseBankStatementDisplay(stmt?.description)
  const cat = String(category || '').trim()
  if (parsed.counterparty && cat) {
    return `${parsed.counterparty} · ${cat}`.slice(0, 50)
  }
  if (cat) {
    const datePart = stmt?.transaction_date ? String(stmt.transaction_date).slice(0, 10) : ''
    return (datePart ? `${cat}-${datePart}` : cat).slice(0, 50)
  }
  return cleanStatementTitle(stmt).slice(0, 50)
}

function isAutoGeneratedManualTitle(title: string): boolean {
  const trimmed = String(title || '').trim()
  return !trimmed || trimmed === manualLastAutoTitle.value
}

function handleManualCategoryChange(val: string) {
  const previousTitle = manualForm.value.title
  manualForm.value.categoryValue = val
  manualSuggestionMeta.value = {}
  const newCategory = String(val || '').split('|')[1] || ''
  if (newCategory && manualTarget.value && isAutoGeneratedManualTitle(previousTitle)) {
    const nextTitle = buildDefaultManualTitle(manualTarget.value, newCategory)
    manualForm.value.title = nextTitle
    manualLastAutoTitle.value = nextTitle
  }
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

// ── 模糊分类候选：正确科目通常取决于账外事实（认缴进度、缴款性质） ──
// 展开规则：附言含糊（如纯「投资款」）时恒展示候选 chips——LLM 置信度会波动，
// 高置信只说明关键词命中，判定不了账外事实；附言自带决断词且高置信时
// （「实缴出资款」坐实股东投入、「溢价/资本公积」坐实资本公积，与后端
// bank_ai_prompts 规则 8 同口径）候选是噪音，不再展开。
// 认可入账以所选分类为准，默认选中建议项。
const rowCategoryChoice = ref<Record<string, string>>({})

// 各分类的附言决断词：命中的账外事实已写在附言里，无需再选
const DECISIVE_OWNER_REMARK: Record<string, RegExp> = {
  股东投入: /出资|实缴|实收/,
  资本公积: /溢价|资本公积/,
  股东垫付: /垫付|代付/,
}

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
  // 确定性对冲（一付一退同落 122199）与微额实名验证打款（<=1 元）不再展开多义项让老板重选
  if (sug.rule === 'refund_pair' || sug.rule === 'micro_auth') return []
  const category = sug.suggestedEntry?.category
  const group = getAmbiguityGroup(category).filter((cat) => categoryDirectionCompatible(stmt, cat))
  if (!group.length) return []
  if (Number(sug.confidence || 0) >= 0.95) {
    const decisive = DECISIVE_OWNER_REMARK[category]
    const remark = `${stmt.description || ''}${sug.suggestedEntry?.title || ''}`
    if (decisive && decisive.test(remark)) return []
  }
  return group
}

// 仍未定论的模糊行：组内 chips 都没有点选过。点选后视为用户已决断，
// 恢复批量确认资格；点选组外分类不走这里（调整科目下拉会直接入账）。
// 高置信度（≥0.95）建议视为 AI 已决断，候选 chips 仅作备选展示，不拦批量。
function isAmbiguityActive(stmt: any): boolean {
  const sug = getAiSuggestion(stmt.id)
  if (!sug || Number(sug.confidence || 0) >= 0.95) return false
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
  const mapped = accountForCategory(chosen)
  if (chosen === '社保公积金' && sug.payrollLiabilitySettlement) {
    const liab = sug.payrollLiabilitySettlement
    const reason = liab.reason || sug.reason
    return {
      ...sug,
      reason,
      payrollSettlement: liab,
      suggestedEntry: {
        ...sug.suggestedEntry,
        category: chosen,
        ...(liab.accountCode || mapped?.code ? { accountCode: liab.accountCode || mapped?.code } : {}),
        ...(liab.accountName || mapped?.name ? { accountName: liab.accountName || mapped?.name } : {}),
        requiresSalarySettlement: true,
        reason,
      },
    }
  }
  if (chosen === '个税代缴' && sug.payrollTaxSettlement) {
    const taxSett = sug.payrollTaxSettlement
    const reason = taxSett.reason || sug.reason
    return {
      ...sug,
      reason,
      payrollSettlement: taxSett,
      suggestedEntry: {
        ...sug.suggestedEntry,
        category: chosen,
        ...(taxSett.accountCode || mapped?.code ? { accountCode: taxSett.accountCode || mapped?.code } : {}),
        ...(taxSett.accountName || mapped?.name ? { accountName: taxSett.accountName || mapped?.name } : {}),
        requiresSalarySettlement: true,
        reason,
      },
    }
  }
  const isPayrollCat = chosen === '工资发放' || chosen === '社保公积金' || chosen === '个税代缴'
  return {
    ...sug,
    ...(isPayrollCat ? {} : { payrollSettlement: undefined }),
    suggestedEntry: {
      ...sug.suggestedEntry,
      category: chosen,
      ...(mapped ? { accountCode: mapped.code, accountName: mapped.name } : {}),
      ...(isPayrollCat ? {} : { requiresSalarySettlement: false }),
    },
  }
}

function altAccountText(stmt: any, category: string): string {
  const preview = suggestionAccountPreview(stmt, category)
  return preview ? `${preview.name} ${preview.code}` : ''
}

function findRefundPairPartner(stmt: any) {
  const sug = getAiSuggestion(stmt.id)
  if (!sug || sug.rule !== 'refund_pair') return null
  if (sug.pairedStatementId) {
    const direct = unmatchedStatements.value.find((s: any) => s.id === sug.pairedStatementId && s.id !== stmt.id)
    if (direct) return direct
  }
  const stmtAmount = Number(stmt.amount) || 0
  return (
    unmatchedStatements.value.find((s: any) => {
      if (s.id === stmt.id) return false
      const otherSug = getAiSuggestion(s.id)
      return otherSug?.rule === 'refund_pair' && Math.abs((Number(s.amount) || 0) + stmtAmount) < 0.01
    }) || null
  )
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

async function openManualClassify(stmt: any, suggestedEntry: any = null, options?: { bypassMatch?: boolean }) {
  const suggestion = getAiSuggestion(stmt.id)
  if (suggestion?.type === 'match' || suggestion?.type === 'existing') {
    if (!options?.bypassMatch) {
      selectExistingReconciliation(stmt, suggestion.matchedEntryIds || [suggestion.matchedEntryId].filter(Boolean))
      return
    }
    const matchedTitle = suggestion?.matchedEntry?.title || '已有单据'
    const confirmed = await new Promise<boolean>((resolve) => {
      dialog.warning({
        title: '确定不配对、改为单独记账？',
        content: `系统已为这笔流水匹配到「${matchedTitle}」。跳过配对并按银行流水直接记账可能会产生重复入账；请仅在确认这笔流水与该单据无关时继续。`,
        positiveText: '继续单独记账',
        negativeText: '取消',
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
        onClose: () => resolve(false),
        onMaskClick: () => resolve(false),
      })
    })
    if (!confirmed) return
  }
  manualTarget.value = stmt
  manualAdvancedOpen.value = false
  manualSuggestionMeta.value = {}
  const sub = suggestedEntry?.suggestedSubAccount
  const initialCategoryValue = categoryValueFromSuggestion(stmt, suggestedEntry)
  const initialCategory = initialCategoryValue.split('|')[1] || String(suggestedEntry?.category || '').trim()
  const initialTitle = buildDefaultManualTitle(stmt, initialCategory)
  manualLastAutoTitle.value = initialTitle
  manualForm.value = {
    title: initialTitle,
    categoryValue: initialCategoryValue,
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
  message.warning('已有账目，请核对匹配单据后确认合并对账，勿重复记账。')
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
  const targetEntry = findEntryById(pair.matchedEntryId) || pair.invoice
  const posted = isPostedEntry(targetEntry)
  if (posted) {
    const confirmed = await new Promise<boolean>((resolve) => {
      dialog.warning({
        title: '确认修改已过账凭证分类',
        content: '该笔凭证此前已过账（如反结账留存）。保存后系统将自动红字冲销原凭证，并按新分类生成正式凭证、同步保持银行流水配对。',
        positiveText: '确认更正',
        negativeText: '取消',
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
        onClose: () => resolve(false),
        onMaskClick: () => resolve(false),
      })
    })
    if (!confirmed) return
  }

  pairCategorySubmitting.value = true
  try {
    await apiClient.patch(`/api/record/entries/${pair.matchedEntryId}`, {
      title: pairCategoryForm.value.title.trim(),
      direction,
      category,
      accountCode,
      accountName,
      ...(posted ? { autoReverseRepost: true } : {}),
      ...(category === '研发支出-费用化支出' ? {
        rdProjectId: pairCategoryForm.value.rdProjectId,
        rdExpenseType: pairCategoryForm.value.rdExpenseType,
        rdOriginalAmount: Math.abs(Number(pair.bank?.amount || pair.invoice?.amount || 0)),
        rdAllocationMethod: 'direct',
        rdAllocationBasis: pairCategoryForm.value.rdAllocationBasis || undefined,
      } : {}),
    })
    showPairCategoryDialog.value = false
    message.success(posted ? `已红冲重记并更新分类：${category}` : `已更新分类：${category}`)
    await reloadAll()
  } catch (err: any) {
    message.error(apiErrorMessage(err, '分类更新失败'))
  } finally {
    pairCategorySubmitting.value = false
  }
}

function openEditVoucher() {
  const entry = currentVoucherEntry.value
  if (!entry) return
  openPendingEntryEdit(entry)
}

/**
 * 「谁付的钱」的弹窗初值：优先沿用第一步已选，其次从现有分录反推。
 * 贷 1002（支出）/ 借 1002（收入）说明钱已经走公司账户；挂账科目按未付/未收处理。
 * 不直接拿 entry.payer 当默认值——历史发票草稿常带 company_account，但分录是 2241。
 */
function initialEditVoucherSettlement(entry: any): { payer: string; paymentStatus: '' | 'unpaid' } {
  const choice = dispositionChoice.value[String(entry?.id)]
  if (choice) {
    return {
      payer: String(choice.payer || 'company_account'),
      paymentStatus: choice.paymentStatus === 'unpaid' ? 'unpaid' : '',
    }
  }
  const payer = String(entry?.payer || '')
  if (payer === 'boss_personal' || payer.startsWith('employee_personal')) {
    return { payer, paymentStatus: '' }
  }
  if (String(entry?.paymentStatus || '') === 'unpaid') {
    return { payer: 'company_account', paymentStatus: 'unpaid' }
  }
  // ai-suggest 候选快照不带分录，但会标记"待核销发票/挂账"
  if (entry?.isPendingInvoice) return { payer: 'company_account', paymentStatus: 'unpaid' }
  const side = entry?.direction === 'income' ? 'debit' : 'credit'
  const codes = ((entry?.voucherLines || {})[side] || []).map((line: any) => String(line.accountCode))
  if (codes.includes('1002')) return { payer: 'company_account', paymentStatus: '' }
  if (codes.some((code: string) => ['2241', '2202', '1122', '1221', '2203'].includes(code))) {
    return { payer: 'company_account', paymentStatus: 'unpaid' }
  }
  // 认不出结算侧（自定义分录）：留空表示"保存时不动付款来源"，
  // 避免手改标题/分类时把用户自己编的分录覆盖成默认科目。
  return { payer, paymentStatus: '' }
}

/** 弹窗里的付款来源 → PATCH/preview 字段；「还没付款」只发 paymentStatus，不带 payer。 */
function editVoucherSettlementPayload(): Record<string, string> {
  const { payer, paymentStatus } = editVoucherForm.value
  if (paymentStatus === 'unpaid') return { paymentStatus: 'unpaid' }
  return payer ? { payer } : {}
}

/** 弹窗内切换付款来源：先本地更新再重算分录预览，所见即所记。 */
function setEditVoucherSettlement(choice: { payer?: string; paymentStatus?: 'unpaid' }) {
  const entry = editVoucherTargetEntry.value || currentVoucherEntry.value
  editVoucherForm.value = {
    ...editVoucherForm.value,
    payer: choice.payer || 'company_account',
    paymentStatus: choice.paymentStatus === 'unpaid' ? 'unpaid' : '',
  }
  if (entry) void loadEditVoucherPreview(entry)
}

// 待确认单据卡片的「修改/详细记账」入口：与过账弹层共用改凭证弹窗与保存逻辑
function openPendingEntryEdit(entry: any, siblings: any[] = []) {
  if (!entry) return
  editVoucherTargetEntry.value = entry
  const targetId = String(entry.id || '')
  const validSiblings = (Array.isArray(siblings) ? siblings : []).filter((item: any) => {
    if (!item?.id) return false
    if (String(item.id) === targetId) return false
    const status = String(item.voucherStatus || item.status || '')
    return status !== 'reversed'
  })
  editVoucherBatchSiblings.value = validSiblings
  editVoucherApplyToSiblings.value = validSiblings.length > 0
  const rdDetail = rdDetailFromEntry(entry)
  // 从第二步进入时带上正在看的分类（含 AI 建议），弹窗与卡片同口径
  const pendingCategoryValue = dispositionCategorySelected.value[String(entry.id)]
  const settlement = initialEditVoucherSettlement(entry)
  editVoucherForm.value = {
    title: entry.title || '',
    categoryValue: pendingCategoryValue || categoryValueFromEntry(entry, null),
    rdProjectId: rdDetail?.projectId || '',
    rdExpenseType: rdDetail?.expenseType || '',
    rdAllocationBasis: rdDetail?.allocationBasis || '',
    payer: settlement.payer,
    paymentStatus: settlement.paymentStatus,
  }
  editVoucherBusinessContext.value = ''
  editVoucherAiSuggestion.value = null
  editVoucherPreview.value = { loading: false }
  showEditVoucherDialog.value = true
  void loadEditVoucherPreview(entry)
}

/** 弹窗内的科目预览：分类一改就重算，保存前先让人看见将落哪个科目 */
async function loadEditVoucherPreview(entry: any) {
  const category = dispositionCategoryOf(editVoucherForm.value.categoryValue)
  editVoucherPreview.value = { loading: true }
  // 研发分类的预览必须带上弹窗里刚填的项目/类别，否则后端按
  // RD_DETAIL_REQUIRED 拒绝，用户会误以为这个分类不可用。
  const rdFields = category === RD_EXPENSE_CATEGORY && editVoucherForm.value.rdProjectId && editVoucherForm.value.rdExpenseType
    ? {
        rdProjectId: editVoucherForm.value.rdProjectId,
        rdExpenseType: editVoucherForm.value.rdExpenseType,
        rdOriginalAmount: String(Math.abs(Number(entry?.amount) || 0)),
        rdAllocationMethod: 'direct',
        ...(editVoucherForm.value.rdAllocationBasis
          ? { rdAllocationBasis: editVoucherForm.value.rdAllocationBasis }
          : {}),
      }
    : {}
  try {
    const base = dispositionPayload(dispositionChoice.value[String(entry.id)], category, rdFields)
    const preview = await previewEntryVoucher(
      String(entry.id),
      // 未过账草稿以弹窗里当前选的付款来源为准（已过账不吃这组字段，避免预览与保存口径打架）
      isPostedEntry(entry) ? base : { ...base, ...editVoucherSettlementPayload() },
    )
    editVoucherPreview.value = { loading: false, preview }
  } catch (err: any) {
    editVoucherPreview.value = {
      loading: false,
      error: apiErrorMessage(err, '暂时无法预览科目'),
      errorCode: String(err?.response?.data?.detail?.code || ''),
    }
  }
}

/** 业务说明 → AI 重新建议分类（不落库），回填到弹窗的分类选择 */
async function refreshEditVoucherAi() {
  const entry = editVoucherTargetEntry.value || currentVoucherEntry.value
  if (!entry) return
  const context = editVoucherBusinessContext.value.trim()
  if (!context) {
    message.warning('请先用一句话描述这笔业务')
    return
  }
  editVoucherAiLoading.value = true
  try {
    const res: any = await apiClient.post(
      `/api/invoice/classify-entry/${entry.id}`,
      { business_context: context },
      { timeout: 60000 },
    )
    const suggestedCategory = String(res?.category || '')
    const suggestedReason = String(res?.reason || '')
    const value = dispositionCategoryValue(
      entry.direction === 'income' ? 'income' : 'expense',
      suggestedCategory,
    )
    if (!value) {
      editVoucherAiSuggestion.value = suggestedReason ? { category: suggestedCategory, reason: suggestedReason } : null
      message.warning('AI 没给出可用的科目建议，请直接选择分类')
      return
    }
    editVoucherForm.value = { ...editVoucherForm.value, categoryValue: value }
    editVoucherAiSuggestion.value = { category: suggestedCategory, reason: suggestedReason }
    await loadEditVoucherPreview(entry)
  } catch (err: any) {
    message.error(apiErrorMessage(err, 'AI 建议失败'))
  } finally {
    editVoucherAiLoading.value = false
  }
}

async function saveEditVoucher() {
  const entry = editVoucherTargetEntry.value || currentVoucherEntry.value
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
  const targetId = String(entry.id)
  const isPosted = String(entry.voucherStatus || entry.status || '') === 'posted'
  const disposition = dispositionChoice.value[targetId]
  // 分录被人工调整过：改分类/付款方式会按规则重生成，必须先让用户确认覆盖
  const warnings = editVoucherPreview.value.preview?.warnings || []
  let confirmRegenerate = false
  if (warnings.length) {
    confirmRegenerate = await new Promise<boolean>((resolve) => {
      dialog.warning({
        title: '分录将被重新生成',
        content: warnings.map((item) => item.message).join('；'),
        positiveText: '继续保存',
        negativeText: '取消',
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
        onClose: () => resolve(false),
        onMaskClick: () => resolve(false),
      })
    })
    if (!confirmRegenerate) return
  }
  editVoucherSaving.value = true
  try {
    // 付款来源以弹窗当前选择为准：发票草稿打开时已从 2241 分录反推为「还没付款」，
    // 不会再随 company_account 默认回落成贷 1002"银行直付"；用户显式改成
    // 老板/员工垫付或公司账户时，也按用户选择重算。
    const payload: Record<string, any> = {
      category,
      ...dispositionPayload(disposition),
      ...(isPosted ? {} : editVoucherSettlementPayload()),
      ...(category === '研发支出-费用化支出' ? {
        rdProjectId: f.rdProjectId,
        rdExpenseType: f.rdExpenseType,
        rdAllocationBasis: f.rdAllocationBasis || undefined,
      } : {}),
    }
    if (isPosted) {
      // 已过账凭证只能红冲重记：标题/方向/自定义科目属于权威层，发过去只会 409
      payload.autoReverseRepost = true
    } else {
      payload.title = f.title.trim()
      payload.direction = direction
      payload.accountCode = accountCode
      payload.accountName = accountName
    }
    if (confirmRegenerate) payload.confirmRegenerateVoucher = true
    const res: any = await apiClient.patch(`/api/record/entries/${entry.id}`, payload)
    const siblingsToUpdate = editVoucherApplyToSiblings.value ? [...editVoucherBatchSiblings.value] : []
    let updatedSiblings = 0
    let failedSiblings = 0
    let anySiblingPosted = false
    for (const sib of siblingsToUpdate) {
      const sibPosted = String(sib.voucherStatus || sib.status || '') === 'posted'
      if (sibPosted) anySiblingPosted = true
      const sibPayload: Record<string, any> = {
        category,
        ...(category === '研发支出-费用化支出' ? {
          rdProjectId: f.rdProjectId,
          rdExpenseType: f.rdExpenseType,
          rdAllocationBasis: f.rdAllocationBasis || undefined,
        } : {}),
      }
      if (sibPosted) {
        sibPayload.autoReverseRepost = true
      } else {
        sibPayload.direction = direction
        sibPayload.accountCode = accountCode
        sibPayload.accountName = accountName
      }
      try {
        await apiClient.patch(`/api/record/entries/${sib.id}`, sibPayload)
        updatedSiblings += 1
      } catch {
        failedSiblings += 1
      }
    }
    if (isPosted || anySiblingPosted || siblingsToUpdate.length > 0) {
      await reloadAll()
    } else {
      await entryStore.fetchEntries()
    }
    showEditVoucherDialog.value = false
    editVoucherTargetEntry.value = null
    editVoucherBatchSiblings.value = []
    editVoucherApplyToSiblings.value = false
    if (disposition) clearDispositionState(targetId)
    if (siblingsToUpdate.length > 0) {
      if (failedSiblings > 0) {
        message.warning(`已更新 ${1 + updatedSiblings} 笔单据分类，另有 ${failedSiblings} 笔更新失败，请刷新后核对`)
      } else {
        message.success(`已批量更新 ${1 + updatedSiblings} 笔单据分类：${category}`)
      }
    } else {
      const summary = voucherLinesSummary(res?.entry?.voucherLines)
      message.success(summary ? `已更新：${summary}` : `已更新分类：${category}`)
    }
    if (editVoucherTargetEntryWasVoucherModal(entry)) {
      openVoucherModal(entry.id)
    }
  } catch (err: any) {
    message.error(apiErrorMessage(err, '修改失败'))
  } finally {
    editVoucherSaving.value = false
  }
}

function editVoucherTargetEntryWasVoucherModal(entry: any): boolean {
  return String(entry.id) === String(currentVoucherEntry.value?.id || '')
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
    const nextCategoryValue = categoryValueFromSuggestion(manualTarget.value, suggested)
    const nextCategory = nextCategoryValue.split('|')[1] || String(suggested.category || '').trim()
    const datePart = String(manualTarget.value?.transaction_date || '').slice(0, 10)
    const rawTitle = String(suggested.title || '').trim()
    const isGenericDated = Boolean(nextCategory && datePart && rawTitle === `${nextCategory}-${datePart}`)
    const nextAutoTitle = (!rawTitle || isGenericDated)
      ? buildDefaultManualTitle(manualTarget.value, nextCategory)
      : rawTitle.slice(0, 50)
    const shouldUpdateTitle = isAutoGeneratedManualTitle(manualForm.value.title)
    if (shouldUpdateTitle) {
      manualLastAutoTitle.value = nextAutoTitle
    }
    manualForm.value = {
      ...manualForm.value,
      title: shouldUpdateTitle ? nextAutoTitle : manualForm.value.title,
      categoryValue: nextCategoryValue,
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
  if (!manualTarget.value || !manualForm.value.categoryValue) {
    message.warning('请选择账目分类')
    return
  }

  const [direction, category, suggestedCode, suggestedName] = manualForm.value.categoryValue.split('|')
  const finalTitle = manualForm.value.title.trim() || buildDefaultManualTitle(manualTarget.value, category)
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
  const submitManual = (allowBackfill: boolean, backfillReason?: string, settleOpeningPayroll = false) =>
    apiClient.post('/api/bank/confirm-suggestion', {
      statement_id: stmt.id,
      type: 'new',
      allowBackfill,
      backfillReason,
      ...(settleOpeningPayroll ? { settleOpeningPayroll: true } : {}),
      new_entry: {
        title: finalTitle,
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
      },
    }, { timeout: 60000 })

  try {
    const confirmRes: any = await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
      submitManual(allowBackfill, backfillReason, false),
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
    } else if (promptPayrollSettlementIntercept(err, async () => {
      await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
        submitManual(allowBackfill, backfillReason, true),
      )
      showManualDialog.value = false
    })) {
      showManualDialog.value = false
    } else {
      message.error(apiErrorMessage(err, '记账提交失败'))
    }
    manualSubmitting.value = false
  }
}


const highConfidenceStatements = computed(() => {
  return unmatchedStatements.value.filter((stmt: any) => {
    const sug = getAiSuggestion(stmt.id)
    if (!sug || isReviewRequiredForStatement(stmt)) return false
    if (Number(sug.confidence || 0) < 0.95 && sug.rule !== 'refund_pair') return false
    if (ambiguityGroupOf(stmt).length > 0 && !rowCategoryChoice.value[stmt.id]) return false
    return true
  })
})

const statementTierFilter = ref<'all' | 'ready' | 'attention'>('all')
const selectedStatementIds = ref<string[]>([])
const rememberRuleOnConfirm = ref(true)

function getPayrollSettlement(stmt: any): any | null {
  const sug = effectiveSuggestion(stmt)
  if (!sug || sug.type !== 'new') return null
  const cat = String(sug.suggestedEntry?.category || '').trim()
  const isPayrollCat = cat === '工资发放' || cat === '社保公积金' || cat === '个税代缴'
  if (sug.payrollSettlement && (isPayrollCat || sug.suggestedEntry?.requiresSalarySettlement)) {
    return sug.payrollSettlement
  }
  if (sug.suggestedEntry?.requiresSalarySettlement || isPayrollCat) {
    return {
      mode: 'salary_module_required',
      kind: cat === '个税代缴' ? 'tax' : cat === '社保公积金' ? 'liability' : 'salary',
      suggestedMonth: '',
    }
  }
  return null
}

/** 该笔流水可冲减的本人垫付余额（后端仅在 2241 有余额且金额可覆盖时下发）。 */
function advancePayableOf(stmt: any): any | null {
  const sug = effectiveSuggestion(stmt)
  if (!sug?.advancePayable) return null
  // 用户改选了其他分类，就不要再展示「将冲减应付」的承诺，以实际入账分类为准。
  if (!['员工垫付报销', '股东垫付报销'].includes(String(sug.suggestedEntry?.category || '').trim())) {
    return null
  }
  return sug.advancePayable
}

/** 发票草稿（company_account）遇上个人报销流水时，后端标记的两步入账建议。 */
function invoiceReimbursementOf(stmt: any): any | null {
  const sug = getAiSuggestion(stmt?.id)
  if (!sug || sug.type !== 'match' || !sug.invoiceReimbursement) return null
  const entry = editableMatchedEntryOf(stmt)
  const payer = String(entry?.payer || '')
  if (payer === 'boss_personal' || payer === 'employee_personal' || payer.startsWith('employee_personal:')) {
    return null
  }
  return sug.invoiceReimbursement
}

/** 找出当前配对（含多票合并）里仍落在兜底科目（其他支出）的有效支出发票记录。 */
function fallbackMatchedEntriesOf(stmt: any): any[] {
  const sug = getAiSuggestion(stmt?.id)
  if (!sug || sug.type !== 'match') return []
  const rawIds: string[] = Array.isArray(sug.matchedEntryIds) && sug.matchedEntryIds.length > 0
    ? sug.matchedEntryIds.map((id: any) => String(id || '')).filter(Boolean)
    : (sug.matchedEntryId ? [String(sug.matchedEntryId)] : [])
  const snapshotMap = new Map<string, any>()
  if (Array.isArray(sug.matchedEntries)) {
    for (const item of sug.matchedEntries) {
      if (item?.id) snapshotMap.set(String(item.id), item)
    }
  }
  if (sug.matchedEntry?.id) {
    snapshotMap.set(String(sug.matchedEntry.id), sug.matchedEntry)
  }
  const activeMap = new Map<string, any>(
    entryStore.activeEntries.map((item: any) => [String(item.id || ''), item]),
  )
  const resolved: any[] = []
  for (const id of rawIds) {
    const entry = activeMap.get(id) || snapshotMap.get(id)
    if (!entry) continue
    const status = String(entry.voucherStatus || entry.status || '')
    if (status === 'reversed') continue
    resolved.push(entry)
  }
  if (resolved.length === 0 && sug.matchedEntry) {
    resolved.push(sug.matchedEntry)
  }
  return resolved.filter((entry: any) => {
    const isExpenseInvoice =
      String(entry.source || sug.matchedEntry?.source || '') === 'invoice_import'
      && String(entry.direction || sug.matchedEntry?.direction || 'expense') === 'expense'
    return (Boolean(sug.fallbackCategory) || isExpenseInvoice) && isFallbackInvoiceCategory(entry.category)
  })
}

/** 已配对单据仍落兜底科目（其他支出）时拦截批量确认，引导老板先通过「修改单据」确定科目。 */
function matchedFallbackCategoryOf(stmt: any): boolean {
  const sug = getAiSuggestion(stmt?.id)
  if (!sug || sug.type !== 'match') return false
  const hasEntries =
    (Array.isArray(sug.matchedEntryIds) && sug.matchedEntryIds.length > 0)
    || Boolean(sug.matchedEntryId)
    || (Array.isArray(sug.matchedEntries) && sug.matchedEntries.length > 0)
    || Boolean(sug.matchedEntry)
  if (hasEntries) {
    return fallbackMatchedEntriesOf(stmt).length > 0
  }
  return Boolean(sug.fallbackCategory)
}

function isReviewRequiredForStatement(stmt: any): boolean {
  const sug = getAiSuggestion(stmt?.id)
  if (!sug) return false
  if (sug.type === 'match' && sug.fallbackCategory && !sug.counterpartyMismatch && !invoiceReimbursementOf(stmt)) {
    return matchedFallbackCategoryOf(stmt)
  }
  return Boolean(sug.requiresReview) || matchedFallbackCategoryOf(stmt)
}

/** 余额来源白话：老板不需要看明细账，但要知道这笔欠款是哪来的。 */
function advancePayableSourceText(stmt: any): string {
  const source = advancePayableOf(stmt)?.sources?.[0]
  if (!source) return ''
  const label = source.label || (source.entryType === 'opening' ? '建账时带入' : '账上垫付')
  return `来源：${label}${source.voucherRef ? ` ${source.voucherRef}` : ''}`
}

const ADVANCE_PAYABLE_CATEGORIES = new Set([
  '员工垫付报销',
  '股东垫付报销',
  '其他应付款',
  '其他应收款',
  '往来款',
])

/** 「不是还旧账，是这次新支出」：按 AI 原分类打开记账对话框，让老板补选费用科目。 */
function handleAdvanceAsNewExpense(stmt: any) {
  const original = getAiSuggestion(stmt.id)?.advancePayable?.original
  const originalCategory = String(original?.category || '').trim()
  // AI 原本也判成往来类时不能拿它去记新支出，交给用户重新选费用分类。
  openManualClassify(stmt, original && !ADVANCE_PAYABLE_CATEGORIES.has(originalCategory) ? original : null)
}

// 批量确认必须按「生效分类」判断：用户点选社保/个税候选后，原建议分类仍是税费缴纳，
// 若只看原始建议会把这类行混进批量，后端岗位拦截会让整批静默失败。
function effectivePayrollKind(stmt: any): string | null {
  const sug = effectiveSuggestion(stmt)
  if (!sug || sug.type !== 'new') return null
  if (sug.payrollSettlement?.kind) return String(sug.payrollSettlement.kind)
  const cat = String(sug.suggestedEntry?.category || '').trim()
  if (cat === '工资发放') return 'salary'
  if (cat === '社保公积金') return 'liability'
  if (cat === '个税代缴') return 'tax'
  if (sug.suggestedEntry?.requiresSalarySettlement) return 'salary'
  return null
}

function isPayrollSettlementSuggestion(stmt: any): boolean {
  return Boolean(getPayrollSettlement(stmt))
}

function isSelectableForBatch(stmt: any): boolean {
  const sug = getAiSuggestion(stmt.id)
  if (!sug) return false
  if (isReviewRequiredForStatement(stmt)) return false
  if (isFallbackSuggestion(stmt)) return false
  if (isPayrollSettlementSuggestion(stmt)) return false
  if (effectivePayrollKind(stmt)) return false
  if (sug.type === 'new' && sug.suggestedEntry?.category === '研发支出-费用化支出') return false
  // 模糊分类行必须逐笔决断（点选候选后才恢复批量资格），不能混进批量静默入账
  if (isAmbiguityActive(stmt)) return false
  return Number(sug.confidence || 0) >= 0.5
}

function isNeedsAttentionStatement(stmt: any): boolean {
  const sug = getAiSuggestion(stmt.id)
  if (sug && isReviewRequiredForStatement(stmt)) return true
  if (findMatchingInvoiceForStatement(stmt)) return false
  if (!sug) return true
  const payroll = getPayrollSettlement(stmt)
  if (payroll && (payroll.mode === 'opening_balance_missing' || payroll.mode === 'salary_module_required')) return true
  if (isFallbackSuggestion(stmt)) return true
  if (ambiguityGroupOf(stmt).length > 0 && !rowCategoryChoice.value[stmt.id]) return true
  return false
}

function suggestionBadgeLabel(stmt: any): string {
  const payroll = getPayrollSettlement(stmt)
  if (payroll?.mode === 'salary_sheet') return '匹配工资表'
  if (payroll?.mode === 'opening_balance') return '冲销期初应付'
  if (payroll?.mode === 'opening_balance_missing') return `期初 ${payroll.accountCode || '221101'} 余额不足`
  if (payroll?.mode === 'salary_module_required') return '需工资模块核销'
  if (isFallbackSuggestion(stmt)) return '待人工指定'
  const sug = getAiSuggestion(stmt.id)
  if (!sug) return '待指定'
  if (sug.advancePayable) return '冲减垫付应付'
  if (invoiceReimbursementOf(stmt)) return '疑似垫付报销'
  if (sug.counterpartyMismatch) return Number(stmt.amount || 0) > 0 ? '对方与购方不一致' : '对方与销方不一致'
  if (matchedFallbackCategoryOf(stmt)) return '待确认科目'
  if (sug.requiresReview) return '规则待复核'
  // 规则类建议展示真实置信度：写死 100% 会误导用户以为行已被完全决断，
  // 而低置信(<0.95)的模糊行勾选框其实被禁用、需点候选 chip 逐笔确认
  const ruleConf = `${Math.round(Number(sug.confidence || 0) * 100)}%`
  if (sug.rule === 'refund_pair') return '一付一退对冲（净额 ¥0.00）'
  if (sug.rule === 'micro_auth') return '小额实名验证'
  if (sug.rule === 'company_memory_rule') return `记忆规则 ${ruleConf}`
  if (sug.rule) return `系统规则 ${ruleConf}`
  return 'AI 推荐'
}

const selectableBatchStatements = computed(() =>
  readyStatements.value.filter((stmt: any) => isSelectableForBatch(stmt))
)

const readyStatements = computed(() => {
  const ready = unmatchedStatements.value.filter((stmt: any) => !isNeedsAttentionStatement(stmt))
  const readyById = new Map(ready.map((stmt: any) => [stmt.id, stmt]))
  const visited = new Set<string>()
  const clustered: any[] = []
  for (const stmt of ready) {
    if (visited.has(stmt.id)) continue
    visited.add(stmt.id)
    clustered.push(stmt)
    const partner = findRefundPairPartner(stmt)
    if (partner && readyById.has(partner.id) && !visited.has(partner.id)) {
      visited.add(partner.id)
      clustered.push(readyById.get(partner.id))
    }
  }
  return clustered
})

const attentionStatements = computed(() =>
  unmatchedStatements.value.filter((stmt: any) => isNeedsAttentionStatement(stmt))
)

const filteredUnmatchedStatements = computed(() => {
  if (statementTierFilter.value === 'ready') {
    return readyStatements.value
  }
  if (statementTierFilter.value === 'attention') {
    return attentionStatements.value
  }
  return [...readyStatements.value, ...attentionStatements.value]
})

watch(
  [unmatchedStatements, aiSuggestions],
  () => {
    const validSelectable = new Set(selectableBatchStatements.value.map((s: any) => s.id))
    // 所有归入「系统有把握」（已排除多义项/兜底/复核）的可确认流水默认全部预勾选，保证顶栏计数与按钮笔数一致
    const defaultReady = readyStatements.value
      .filter((s: any) => validSelectable.has(s.id))
      .map((s: any) => s.id)
    const kept = selectedStatementIds.value.filter((id) => validSelectable.has(id))
    selectedStatementIds.value = kept.length > 0 ? kept : defaultReady
  },
  { immediate: true }
)

function toggleStatementSelection(stmtId: string, checked: boolean) {
  const targetStmt = unmatchedStatements.value.find((s: any) => s.id === stmtId)
  const partner = targetStmt ? findRefundPairPartner(targetStmt) : null
  const affectedIds = partner && isSelectableForBatch(partner) ? [stmtId, partner.id] : [stmtId]
  if (checked) {
    const next = [...selectedStatementIds.value]
    for (const id of affectedIds) {
      if (!next.includes(id)) next.push(id)
    }
    selectedStatementIds.value = next
  } else {
    const removeSet = new Set(affectedIds)
    selectedStatementIds.value = selectedStatementIds.value.filter((id) => !removeSet.has(id))
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
  return picked.length > 0
    ? picked
    : readyStatements.value.filter((stmt: any) => isSelectableForBatch(stmt))
})

/** 按建议确认单笔流水（match 关联已有记录 / new 新建入账）。批量与单条共用同一路径。 */
async function confirmSuggested(
  stmt: any,
  sug: any,
  opts: {
    skipDuplicateCheck?: boolean
    rememberRule?: boolean
    settleOpeningPayroll?: boolean
    reimbursementPayer?: string
  } = {}
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
        ...(opts.reimbursementPayer ? { reimbursementPayer: opts.reimbursementPayer } : {}),
      })
    )
  }
  const suggestedEntry = sug.suggestedEntry || {}
  // 垫付报销等往来分类的方向必须保留 owner_transfer：按流水正负硬判成 expense 会把
  // 2241 冲减凭证改成费用凭证（后端会按 category 重算借贷，方向错了科目就错了）。
  const suggestedDirection = String(suggestedEntry.direction || '').trim()
  const direction = suggestedDirection === 'owner_transfer'
    ? 'owner_transfer'
    : (stmt.amount > 0 ? 'income' : 'expense')
  return await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
    apiClient.post('/api/bank/confirm-suggestion', {
      statement_id: stmt.id,
      type: 'new',
      new_entry: {
        title: suggestedEntry.title || cleanStatementTitle(stmt),
        category: suggestedEntry.category || (stmt.amount > 0 ? '其他业务收入' : '其他支出'),
        amount: Math.abs(stmt.amount),
        direction,
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
      ...(opts.settleOpeningPayroll ? { settleOpeningPayroll: true } : {}),
    })
  )
}

interface LastConfirmedItem {
  statementId: string
  type: 'match' | 'new'
  createdEntryId?: string
  title?: string
}

const lastConfirmedAction = ref<{ label: string; items: LastConfirmedItem[] } | null>(null)
const undoingLastConfirm = ref(false)

async function undoLastConfirmed() {
  const action = lastConfirmedAction.value
  if (!action || !action.items.length || undoingLastConfirm.value) return
  undoingLastConfirm.value = true
  try {
    const entriesById = new Map(entryStore.activeEntries.map((e: any) => [String(e.id), e]))
    const toRemove: any[] = []
    for (const item of action.items) {
      await apiClient.post('/api/bank/confirm-suggestion', {
        statement_id: item.statementId,
        type: 'match',
        matched_entry_id: '',
        matched_entry_ids: [],
      })
      if (item.type === 'new' && item.createdEntryId) {
        const existing = entriesById.get(String(item.createdEntryId))
        toRemove.push(existing || { id: item.createdEntryId, title: item.title || item.createdEntryId, source: 'bank_import' })
      }
    }
    const failed = toRemove.length ? await removeBankCreatedEntries(toRemove) : []
    lastConfirmedAction.value = null
    await reloadAll()
    if (failed.length) {
      message.warning(`流水已退回待处理，部分凭证需到账目页冲销：${failed.join('、')}`)
    } else {
      message.success('已撤销刚才的确认，流水已回到待处理列表')
    }
  } catch (err: any) {
    message.error(apiErrorMessage(err, '撤销失败'))
  } finally {
    undoingLastConfirm.value = false
  }
}

function dismissLastConfirmed() {
  lastConfirmedAction.value = null
}

const batchConfirming = ref(false)
function shouldRememberSuggestion(sug: any, stmt?: any): boolean {
  const category = String(sug?.suggestedEntry?.category || '')
  const rawCategory = stmt
    ? String(getAiSuggestion(stmt.id)?.suggestedEntry?.category || '')
    : ''
  return Boolean(
    rememberRuleOnConfirm.value
    && sug?.type === 'new'
    && sug.rule !== 'refund_pair'
    && !sug.requiresReview
    && !getAmbiguityGroup(category).length
    && !(rawCategory && getAmbiguityGroup(rawCategory).length)
    && !(stmt && ambiguityGroupOf(stmt).length)
  )
}

async function handleBatchConfirmRules() {
  const items = batchTargetStatements.value
    .map((stmt: any) => ({ stmt, sug: effectiveSuggestion(stmt) }))
    .filter((item: any) => item.sug)
  if (items.length === 0) return
  batchConfirming.value = true
  let confirmed = 0
  let cancelled = false
  const confirmedItems: LastConfirmedItem[] = []
  try {
    // 串行执行：跨期补记弹窗共用同一个 resolver，并发会互相覆盖
    for (const { stmt, sug } of items) {
      try {
        const rememberRule = shouldRememberSuggestion(sug, stmt)
        const res: any = await confirmSuggested(stmt, sug, { rememberRule })
        if (res?.ruleWarning) message.warning(res.ruleWarning)
        confirmed += 1
        confirmedItems.push({
          statementId: stmt.id,
          type: sug.type === 'match' ? 'match' : 'new',
          createdEntryId: sug.type === 'new' ? (res?.entryId || res?.entry?.id) : undefined,
          title: sug.suggestedEntry?.title || cleanStatementTitle(stmt),
        })
      } catch (err: any) {
        if (isBackfillCancelled(err)) {
          cancelled = true
          break
        }
        console.error('批量确认失败:', stmt.id, err)
      }
    }
    if (confirmedItems.length > 0) {
      lastConfirmedAction.value = {
        label: confirmedItems.length === 1
          ? `「${confirmedItems[0].title || '1 笔流水'}」`
          : `${confirmedItems.length} 笔流水`,
        items: confirmedItems,
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

function formatOpeningPayrollLabel(opening: any): string {
  const allocs = Array.isArray(opening?.allocations) ? opening.allocations : []
  if (allocs.length > 1) {
    return allocs.map((a: any) => `${a.accountCode} ${a.accountName} ¥${a.amount}`).join(' + ')
  }
  return `${opening?.accountCode || ''} ${opening?.accountName || ''}`.trim()
}

function promptPayrollSettlementIntercept(
  err: any,
  retryOpening?: () => Promise<void>,
): boolean {
  const detail = err?.response?.data?.detail
  const code = detail?.code
  if (code !== 'SALARY_REQUIRES_PAYROLL' && code !== 'PAYROLL_LIABILITY_REQUIRES_PAYROLL' && code !== 'PAYROLL_TAX_REQUIRES_PAYROLL') {
    return false
  }
  const opening = detail?.openingPayroll
  if (opening?.canSettle && retryOpening) {
    const label = formatOpeningPayrollLabel(opening)
    dialog.info({
      title: '可冲销开账前期初应付',
      content: `该笔流水属于建账日（${opening.bookStartDate}）前遗留应付，将冲销期初「${label}」（可用贷方余额合计 ¥${opening.availableOpeningCredit}），是否直接冲销期初负债并完成对账？`,
      positiveText: '立即冲销期初应付',
      negativeText: '取消',
      onPositiveClick: async () => {
        try {
          await retryOpening()
          message.success(`已冲销期初「${label}」余额并完成对账`)
          await reloadAll()
        } catch (retryErr: any) {
          if (!isBackfillCancelled(retryErr)) {
            message.error(apiErrorMessage(retryErr, '冲销期初应付失败'))
          }
        }
      },
    })
    return true
  }
  if (opening?.eligible) {
    dialog.warning({
      title: '期初应付余额不足',
      content: detail?.message || `开账首月发放前期应付需先在「期初设置」补录「${opening.accountCode} ${opening.accountName}」贷方期初余额。`,
      positiveText: '前往期初设置补录',
      negativeText: '关闭',
      onPositiveClick: () => {
        router.push('/mine/opening')
      },
    })
    return true
  }
  dialog.warning({
    title: '需通过工资模块核销',
    content: detail?.message || '工资及社保公积金扣款需在工资模块先计提并按流水核销，不能直接记费用。',
    positiveText: '前往工资模块',
    negativeText: '关闭',
    onPositiveClick: () => {
      router.push('/mine/salary')
    },
  })
  return true
}

async function handlePayrollSettlementAction(stmt: any) {
  const payroll = getPayrollSettlement(stmt)
  const sug = effectiveSuggestion(stmt)
  if (!payroll) return
  if (payroll.mode === 'opening_balance_missing') {
    router.push('/mine/opening')
    return
  }
  if (payroll.mode === 'salary_module_required') {
    router.push({
      path: '/mine/salary',
      query: payroll.suggestedMonth ? { month: payroll.suggestedMonth } : {},
    })
    return
  }
  quickSubmittingId.value = stmt.id
  try {
    if (payroll.mode === 'salary_sheet') {
      const res: any = payroll.kind === 'salary'
        ? await applySalaryPayment(payroll.month, stmt.id)
        : await applyPayrollLiability(payroll.month, payroll.kind, stmt.id)
      await entryStore.fetchEntries()
      message.success(
        res?.linked
          ? `${payroll.month} ${payroll.label || '工资'}已核销并生成凭证`
          : `${payroll.label || '工资'}已确认，请检查流水关联`,
      )
      await reloadAll()
      return
    }
    if (payroll.mode === 'opening_balance' && sug) {
      await confirmSuggested(stmt, sug, { settleOpeningPayroll: true })
      message.success(`已冲销期初「${formatOpeningPayrollLabel(payroll)}」余额并完成对账`)
      await reloadAll()
    }
  } catch (err: any) {
    if (isBackfillCancelled(err)) return
    if (promptPayrollSettlementIntercept(err)) return
    message.error(apiErrorMessage(err, '工资/社保核销失败'))
  } finally {
    quickSubmittingId.value = null
  }
}

async function handleAcceptSuggested(stmt: any) {
  // 用户点选过候选 chips 时以所选分类为准
  const sug = effectiveSuggestion(stmt)
  if (!sug) return
  const partner = findRefundPairPartner(stmt)
  const partnerSug = partner ? effectiveSuggestion(partner) : null
  quickSubmittingId.value = stmt.id
  // 模糊组、退款配对和需复核建议都不能沉淀为自动规则。
  const shouldRemember = Boolean(
    shouldRememberSuggestion(sug, stmt)
    && !isFallbackSuggestion(stmt)
  )
  try {
    const res: any = await confirmSuggested(stmt, sug, { rememberRule: shouldRemember })
    const suggestedEntry = sug.suggestedEntry || {}
    const confirmedItems: LastConfirmedItem[] = [
      {
        statementId: stmt.id,
        type: sug.type === 'match' ? 'match' : 'new',
        createdEntryId: sug.type === 'new' ? (res?.entryId || res?.entry?.id) : undefined,
        title: suggestedEntry.title || cleanStatementTitle(stmt),
      },
    ]
    if (partner && partnerSug) {
      const partnerRes: any = await confirmSuggested(partner, partnerSug, { rememberRule: false })
      confirmedItems.push({
        statementId: partner.id,
        type: 'new',
        createdEntryId: partnerRes?.entryId || partnerRes?.entry?.id,
        title: partnerSug.suggestedEntry?.title || cleanStatementTitle(partner),
      })
    }
    lastConfirmedAction.value = {
      label: partner && partnerSug
        ? `「${cleanStatementTitle(stmt) || stmt.description}」一付一退 2 笔对冲`
        : `「${cleanStatementTitle(stmt) || stmt.description}」`,
      items: confirmedItems,
    }
    if (partner && partnerSug) {
      message.success('已成对冲抵入账（2 笔往来互相冲销，净影响 ¥0.00）')
    } else if (sug.type === 'match') {
      message.success(`已完成对账：「${stmt.description}」`)
    } else if (res?.savedRulePattern) {
      message.success(`已确认入账并对账：「${stmt.description}」→ ${suggestedEntry.category}（已沉淀规则：后续「${res.savedRulePattern}」自动归入此科目）`)
    } else {
      message.success(`已确认入账并对账：「${stmt.description}」→ ${suggestedEntry.category}`)
    }
    if (res?.ruleWarning) message.warning(res.ruleWarning)
    await reloadAll()
  } catch (err: any) {
    if (isBackfillCancelled(err)) return
    if (promptPayrollSettlementIntercept(err, () => confirmSuggested(stmt, sug, { settleOpeningPayroll: true }).then(() => undefined))) return
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
  const submitNewEntry = (
    allowBackfill: boolean,
    backfillReason?: string,
    skipDuplicateCheck = false,
    settleOpeningPayroll = false,
  ) =>
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
      ...(settleOpeningPayroll ? { settleOpeningPayroll: true } : {}),
      // 模糊组分类不沉淀规则：同一附言在不同事实下应落不同科目
      ...(rememberRuleOnConfirm.value && !getAmbiguityGroup(category).length ? { rememberRule: true } : {}),
    })
  try {
    const res: any = await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
      submitNewEntry(allowBackfill, backfillReason)
    )
    lastConfirmedAction.value = {
      label: `「${cleanStatementTitle(stmt) || stmt.description}」→ ${category}`,
      items: [
        {
          statementId: stmt.id,
          type: 'new',
          createdEntryId: res?.entryId || res?.entry?.id,
          title: cleanStatementTitle(stmt),
        },
      ],
    }
    if (res?.savedRulePattern) {
      message.success(`已入账：「${stmt.description}」→ ${category}（已沉淀规则：后续「${res.savedRulePattern}」自动归入此科目）`)
    } else {
      message.success(`已入账：「${stmt.description}」→ ${category}`)
    }
    await reloadAll()
  } catch (err: any) {
    if (isBackfillCancelled(err)) return
    if (promptPayrollSettlementIntercept(err, () => backfillConfirm.withBackfill((allowBackfill, backfillReason) => submitNewEntry(allowBackfill, backfillReason, false, true)).then(() => undefined))) return
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
    return Math.abs(stmtAmount - entryAmount) < 0.01
      && isDirectionCompatible(entry.direction, stmt.amount)
      // 「吻合发票」必须同时通过销方↔对手方亲和：金额巧合（社保金库 vs
      // 科技公司发票）不能叫"吻合"
      && statementSellerAffinity(stmt, entry)
  })
}

// ── 待确认账目（发票/手工账）：老板只需要回答"付款了吗" ─────────────────

/** 给一笔账目找最可能的银行流水：方向一致、日期 ±10 天、金额一致（±1 分），
 *  且发票销方与流水对手方相关。
 *  金额容差已移除（回归 2026-10：±10% 容差让 ¥210 发票与 ¥200 流水互相暗示，
 *  同一张流水被两张金额相近的发票同时认领）。金额有差额的配对交给流水侧的
 *  Tier 3 低置信建议（明示「建议人工核实」，一张流水只推一个最优候选）。 */
function findStatementCandidateForEntry(entry: any) {
  const entryAmount = Math.abs(Number(entry.amount) || 0)
  const entryDate = String(entry.date || entry.occurDate || '').slice(0, 10)
  const wantsIncome = entry.direction === 'income'
  let best: { statement: any; diff: number } | null = null
  for (const stmt of unmatchedStatements.value) {
    // 已被标为个人垫付报销的流水应由用户先确认发票垫付人与分类，不推成公户直接付款
    if (invoiceReimbursementOf(stmt)) continue
    const stmtAmount = Number(stmt.amount) || 0
    if ((stmtAmount > 0) !== wantsIncome) continue
    const stmtDate = String(stmt.transaction_date || '').slice(0, 10)
    if (entryDate && stmtDate) {
      const dayDiff = Math.abs(new Date(entryDate).getTime() - new Date(stmtDate).getTime()) / 86400000
      if (dayDiff > 10) continue
    }
    const diff = Math.abs(Math.abs(stmtAmount) - entryAmount)
    // 只推金额一致的候选：金额接近的暗示一半是错的，宁缺毋滥
    if (diff >= 0.01) continue
    // 销方 ↔ 对手方守门：金额一致但毫无关系（社保金库 vs 科技公司发票）
    // 也不能当「疑似对应」提示
    if (!statementSellerAffinity(stmt, entry)) continue
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

/** 账目已付款：命中当月疑似流水时直接触发配对核销，找不到时提示补导或改选去向。 */
async function handleEntryPaid(entry: any) {
  const candidate = findStatementCandidateForEntry(entry)
  currentStep.value = 2
  if (!candidate) {
    message.warning('本月没找到对应流水。如果这笔是现金或个人账户支付，可以标记「还没付款」或直接忽略。')
    return
  }
  await handleMatchInvoiceDirect(candidate.statement, entry)
}

/** ── 待确认款项两步流：第一步选"谁付的钱"，第二步确认"算什么支出" ── */
type DispositionChoice = { payer?: string; paymentStatus?: 'unpaid'; label: string }
type DispositionPreviewState = {
  loading: boolean
  /** 过期响应守卫：连点换分类时只认最后一次请求 */
  requestId: number
  category: string
  preview?: VoucherPreview
  error?: string
  errorCode?: string
}

const expandedDispositionEntryId = ref<string | null>(null)
const dispositionChoice = ref<Record<string, DispositionChoice>>({})
const dispositionRecommendation = ref<Record<string, { category: string; reason: string } | 'loading' | 'none'>>({})
const dispositionCategorySelected = ref<Record<string, string>>({})
const dispositionResettingId = ref<string | null>(null)
const dispositionManualCategory = ref<Record<string, boolean>>({})
const dispositionPreview = ref<Record<string, DispositionPreviewState>>({})
const dispositionSubmittingId = ref<string | null>(null)

/**
 * 第二步卡片内就地归集的研发明细（分类选「研发支出-费用化支出」时出现）。
 * 与「详细记账」弹窗共用后端字段，只是省掉一次弹窗跳转。
 */
type DispositionRdChoice = {
  projectId: string
  expenseType: RdExpenseType | ''
  allocationBasis: string
}
const dispositionRdChoice = ref<Record<string, DispositionRdChoice>>({})

function emptyRdChoice(): DispositionRdChoice {
  return { projectId: '', expenseType: '', allocationBasis: '' }
}

/** 兜底一份空白研发明细，避免模板里对未初始化的 entry 做非空断言。 */
function rdChoiceOf(entry: any): DispositionRdChoice {
  return dispositionRdChoice.value[String(entry?.id)] || emptyRdChoice()
}

/**
 * 分类名 → CATEGORY_OPTIONS 的选项值；第二步不收的分类（工资/社保/平台结算）返回空。
 * 研发支出现在可以内联归集，但只在账号内有覆盖本月的有效项目时开放——
 * 否则会引导用户选中一个补不齐明细的分类。
 */
function dispositionCategoryValue(direction: string, category: string): string {
  if (!category || DISPOSITION_EXCLUDED_CATEGORIES.has(category)) return ''
  if (category === RD_EXPENSE_CATEGORY && !rdCategoryEnabled.value) return ''
  return (
    CATEGORY_OPTIONS.value.find(
      (opt: any) => !opt.disabled && String(opt.value).startsWith(`${direction}|${category}|`),
    )?.value || ''
  )
}

/** 研发明细只在分类确实是研发支出、且三项必填齐全时透传，避免污染其他分类的请求。 */
function dispositionRdPayload(
  entry: any,
  category?: string,
): Record<string, string> {
  // 内联归集只对草稿开放：已过账凭证的研发明细必须在「详细记账」里显式确认，
  // 这里重算一份 rdOriginalAmount/分摊方法反而会和原凭证的归集口径打架。
  if (String(category || '') !== RD_EXPENSE_CATEGORY || !dispositionRdEnabled(entry)) return {}
  const id = String(entry.id)
  const rd = dispositionRdChoice.value[id]
  if (!rd?.projectId || !rd.expenseType) return {}
  return {
    rdProjectId: rd.projectId,
    rdExpenseType: rd.expenseType,
    // 卡片内只做 100% 直归；共用费用按比例分摊走「详细记账」弹窗，
    // 那里才有金额拆分与分摊方法选择。
    rdOriginalAmount: String(Math.abs(Number(entry.amount) || 0)),
    rdAllocationMethod: 'direct',
    ...(rd.allocationBasis ? { rdAllocationBasis: rd.allocationBasis } : {}),
  }
}

function dispositionPayload(
  choice?: DispositionChoice,
  category?: string,
  rdFields: Record<string, string> = {},
): Record<string, string> {
  return {
    ...(choice?.payer ? { payer: choice.payer } : {}),
    ...(choice?.paymentStatus ? { paymentStatus: choice.paymentStatus } : {}),
    ...(category ? { category } : {}),
    ...rdFields,
  }
}

/** 研发归集是否已补齐必填项（项目 + 费用类别）。分摊依据仅共用费用时才必填。 */
function dispositionRdMissing(entry: any): boolean {
  if (dispositionCategoryOf(dispositionCategorySelected.value[String(entry.id)] || '') !== RD_EXPENSE_CATEGORY) {
    return false
  }
  const rd = dispositionRdChoice.value[String(entry.id)]
  return !(rd?.projectId && rd.expenseType)
}

function isPostedEntry(entry: any): boolean {
  return String(entry?.voucherStatus || entry?.status || '') === 'posted'
}

/**
 * 卡片内联研发归集的开放条件：账号内有覆盖本月的有效项目，且凭证未过账。
 * 已过账凭证带研发明细会走 update_draft 的冲销重记分支，而 rdProjectId 不在
 * POSTED_PROJECTION_FIELDS 白名单里，后端必然 409——与其点了才报错，不如不给选项。
 */
function dispositionRdEnabled(entry: any): boolean {
  return rdCategoryEnabled.value && !isPostedEntry(entry)
}

/** 单项目账套默认直归：选中分类即预填项目，少一次点击。 */
function defaultDispositionRdChoice(entry: any): DispositionRdChoice {
  const existing = rdDetailFromEntry(entry)
  const fromEntry: DispositionRdChoice = {
    projectId: String(existing?.projectId || ''),
    expenseType: (existing?.expenseType || '') as RdExpenseType | '',
    allocationBasis: String(existing?.allocationBasis || ''),
  }
  if (fromEntry.projectId && fromEntry.expenseType) return fromEntry
  const only = effectiveRdProjectOptions.value
  return {
    ...fromEntry,
    projectId: fromEntry.projectId || (only.length === 1 ? String(only[0].value) : ''),
  }
}

function ensureDispositionRdChoice(entry: any, reset = false) {
  const id = String(entry.id)
  if (!reset && dispositionRdChoice.value[id]) return
  dispositionRdChoice.value = { ...dispositionRdChoice.value, [id]: defaultDispositionRdChoice(entry) }
}

function setDispositionRdField(
  entry: any,
  field: keyof DispositionRdChoice,
  value: string,
) {
  const id = String(entry.id)
  ensureDispositionRdChoice(entry)
  dispositionRdChoice.value = {
    ...dispositionRdChoice.value,
    [id]: { ...dispositionRdChoice.value[id], [field]: value },
  }
  void loadDispositionPreview(entry)
}

/**
 * 第二步科目预览：与「认可入账」共用后端规则，所见即所记。
 * 预览失败不阻塞流程（页面退化为分类名 + 科目代码），但保留错误原因。
 */
async function loadDispositionPreview(entry: any, category?: string) {
  const id = String(entry.id)
  const choice = dispositionChoice.value[id]
  const targetValue = String(category || dispositionCategorySelected.value[id] || '')
  const targetCategory = dispositionCategoryOf(targetValue) || String(entry.category || '')
  if (targetCategory === RD_EXPENSE_CATEGORY) ensureDispositionRdChoice(entry)
  const requestId = (dispositionPreview.value[id]?.requestId || 0) + 1
  dispositionPreview.value = {
    ...dispositionPreview.value,
    [id]: { loading: true, requestId, category: targetCategory },
  }
  try {
    const preview = await previewEntryVoucher(
      id,
      dispositionPayload(choice, targetCategory, dispositionRdPayload(entry, targetCategory)),
    )
    const state = dispositionPreview.value[id]
    if (!state || state.requestId !== requestId) return
    dispositionPreview.value = {
      ...dispositionPreview.value,
      [id]: { loading: false, requestId, category: preview.category, preview },
    }
  } catch (err: any) {
    const state = dispositionPreview.value[id]
    if (!state || state.requestId !== requestId) return
    dispositionPreview.value = {
      ...dispositionPreview.value,
      [id]: {
        loading: false,
        requestId,
        category: targetCategory,
        error: apiErrorMessage(err, '暂时无法预览科目'),
        errorCode: String(err?.response?.data?.detail?.code || ''),
      },
    }
  }
}

const priorConflictAcknowledged = ref<Record<string, boolean>>({})

function openDispositionPanel(entry: any, choice: DispositionChoice) {
  const id = String(entry.id)
  dispositionChoice.value = { ...dispositionChoice.value, [id]: choice }
  expandedDispositionEntryId.value = id
  if (!dispositionCategorySelected.value[id]) {
    dispositionCategorySelected.value = {
      ...dispositionCategorySelected.value,
      [id]: dispositionCategoryValue(
        entry.direction === 'income' ? 'income' : 'expense',
        String(entry.category || ''),
      ),
    }
  }
  void loadDispositionPreview(entry)
  void loadDispositionRecommendation(entry)
}

/**
 * 第一步：记录资金来源选择并就地展开第二步（不落库）。
 * 若检测到同销方同金额前期公户付款且用户选了垫付/未付款，先拦截引导改挂前期凭证防重复入账。
 */
function beginDisposition(entry: any, choice: DispositionChoice) {
  const id = String(entry.id)
  const isDuplicateRiskChoice =
    entry?.direction === 'expense'
    && (
      choice.paymentStatus === 'unpaid'
      || choice.payer === 'boss_personal'
      || String(choice.payer || '').startsWith('employee_personal')
    )
  const priorCand = isDuplicateRiskChoice ? priorPaidSuggestionOf(entry) : null
  if (priorCand && !priorConflictAcknowledged.value[id]) {
    const amountVal = Number(priorCand.amount) || Number(entry.amount) || 0
    dialog.warning({
      title: '检测到前期公户可能已付过这笔钱',
      content: `系统查到 ${priorPaidCandidateTitle(priorCand)} 已有同销方、同金额（${formatMoney(amountVal)}）的公户付款记录。继续记为「${choice.label}」会造成费用重复记账并虚增往来余额，建议直接挂接到前期付款凭证。`,
      positiveText: '改挂到前期付款（推荐）',
      negativeText: '确为另一笔，继续选择',
      onPositiveClick: () => executeLinkToPriorPayment(entry, priorCand),
      onNegativeClick: () => {
        priorConflictAcknowledged.value = { ...priorConflictAcknowledged.value, [id]: true }
        openDispositionPanel(entry, choice)
      },
    })
    return
  }
  openDispositionPanel(entry, choice)
}

/** AI 建议：复用 classify-entry（不落库）；每张单据只问一次，失败静默保留当前分类 */
async function loadDispositionRecommendation(entry: any) {
  const id = String(entry.id)
  if (id in dispositionRecommendation.value) return
  dispositionRecommendation.value = { ...dispositionRecommendation.value, [id]: 'loading' }
  try {
    const res: any = await apiClient.post(`/api/invoice/classify-entry/${id}`, {})
    const category = String(res?.category || '')
    const value = dispositionCategoryValue(
      entry.direction === 'income' ? 'income' : 'expense',
      category,
    )
    if (!value) {
      dispositionRecommendation.value = { ...dispositionRecommendation.value, [id]: 'none' }
      return
    }
    dispositionRecommendation.value = {
      ...dispositionRecommendation.value,
      [id]: { category, reason: String(res?.reason || '') },
    }
    // 用户已手动换过分类就不再被 AI 覆盖
    if (!dispositionManualCategory.value[id]) {
      dispositionCategorySelected.value = { ...dispositionCategorySelected.value, [id]: value }
      void loadDispositionPreview(entry, value)
    }
  } catch {
    dispositionRecommendation.value = { ...dispositionRecommendation.value, [id]: 'none' }
  }
}

function dispositionRecommendedValue(entry: any): string {
  const rec = dispositionRecommendation.value[String(entry.id)]
  if (!rec || rec === 'loading' || rec === 'none' || !rec.category) return ''
  return dispositionCategoryValue(
    entry.direction === 'income' ? 'income' : 'expense',
    rec.category,
  )
}

function dispositionRecommendationReason(entry: any): string {
  const rec = dispositionRecommendation.value[String(entry.id)]
  return rec && rec !== 'loading' && rec !== 'none' ? rec.reason : ''
}

/** 第二步预览结果（模板用，避免在模板里写非空断言） */
function dispositionPreviewOf(entry: any): VoucherPreview | null {
  return dispositionPreview.value[String(entry.id)]?.preview || null
}

function dispositionPreviewDebitLines(entry: any) {
  return dispositionPreviewOf(entry)?.debit || []
}

function dispositionPreviewCreditLines(entry: any) {
  return dispositionPreviewOf(entry)?.credit || []
}

/** 第二步换分类的 NSelect 分组选项（naive-ui 需要 type/children 形状） */
function dispositionCategorySelectGroups(entry: any) {
  const direction: 'income' | 'expense' = entry?.direction === 'income' ? 'income' : 'expense'
  return dispositionCategoryGroups(CATEGORY_OPTIONS.value, direction, dispositionRdEnabled(entry)).map((group, index) => ({
    type: 'group' as const,
    key: `disposition-group-${index}-${group.label}`,
    label: group.label,
    children: group.options.map((opt) => ({ label: opt.label, value: opt.value })),
  }))
}

/** 第二步换分类：重新预览（不落库） */
function setDispositionCategory(entry: any, value: string) {
  const id = String(entry.id)
  dispositionCategorySelected.value = { ...dispositionCategorySelected.value, [id]: value }
  dispositionManualCategory.value = { ...dispositionManualCategory.value, [id]: true }
  // 切换到研发分类时补一份可编辑的研发明细；切走则保留已填内容以便切回
  if (dispositionCategoryOf(value) === RD_EXPENSE_CATEGORY) ensureDispositionRdChoice(entry)
  void loadDispositionPreview(entry, value)
}

function clearDispositionState(id: string) {
  if (expandedDispositionEntryId.value === id) expandedDispositionEntryId.value = null
  delete dispositionChoice.value[id]
  delete dispositionRecommendation.value[id]
  delete dispositionCategorySelected.value[id]
  delete dispositionManualCategory.value[id]
  delete dispositionPreview.value[id]
  delete dispositionRdChoice.value[id]
  delete priorConflictAcknowledged.value[id]
}

/** 第二步确认：一次 PATCH 同时落资金来源与科目，成功后单据离开待确认列表 */
async function acceptPendingDisposition(entry: any) {
  const id = String(entry.id)
  const choice = dispositionChoice.value[id]
  if (!choice) return
  const category = dispositionCategoryOf(dispositionCategorySelected.value[id] || '')
  if (!category) {
    // 分类下拉里选不到研发（账号内没有覆盖本月的有效项目）时，才转「详细记账」
    if (
      String(entry.category || '') === RD_EXPENSE_CATEGORY
      || dispositionPreview.value[id]?.errorCode === 'RD_DETAIL_REQUIRED'
    ) {
      message.info('这类支出需要补研发项目与费用类别，请用「详细记账」确认')
      openPendingEntryEdit(entry)
      return
    }
    message.warning('请先选择这笔算什么支出/收入')
    return
  }
  if (DISPOSITION_EXCLUDED_CATEGORIES.has(category)) {
    message.warning('这个分类需要补充明细，请用「详细记账」确认')
    openPendingEntryEdit(entry)
    return
  }
  // 研发分类已常驻，但明细没补齐时不能落账——后端会以 RD_DETAIL_REQUIRED 422 拦下
  if (category === RD_EXPENSE_CATEGORY && dispositionRdMissing(entry)) {
    message.warning('请先选择研发项目和费用类别')
    return
  }
  const rawWarnings = dispositionPreview.value[id]?.preview?.warnings || []
  const warnings = priorConflictAcknowledged.value[id]
    ? rawWarnings.filter((w) => w.code !== 'PRIOR_PAYMENT_DUPLICATE_RISK')
    : rawWarnings
  let confirmRegenerate = false
  if (warnings.length) {
    const onlyPriorWarning = warnings.every((w) => w.code === 'PRIOR_PAYMENT_DUPLICATE_RISK')
    const confirmed = await new Promise<boolean>((resolve) => {
      dialog.warning({
        title: onlyPriorWarning ? '疑似与前期公户付款重复入账' : '分录将被重新生成',
        content: warnings.map((item) => item.message).join('；'),
        positiveText: '继续入账',
        negativeText: '取消',
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
        onClose: () => resolve(false),
        onMaskClick: () => resolve(false),
      })
    })
    if (!confirmed) return
    confirmRegenerate = warnings.some((w) => w.code !== 'PRIOR_PAYMENT_DUPLICATE_RISK')
  }
  dispositionSubmittingId.value = id
  try {
    // 已过账凭证只能红冲重记；研发明细属于权威字段，不在投影白名单内，
    // 后端对已过账凭证会以 409 拒绝，所以内联归集只对草稿开放（见卡片 UI）。
    const res: any = await updateEntry(entry.id, {
      ...dispositionPayload(choice, category, dispositionRdPayload(entry, category)),
      autoReverseRepost: true,
      ...(confirmRegenerate ? { confirmRegenerateVoucher: true } : {}),
    })
    // 去向+科目重算会重建分录（如老板垫付 → 贷 2241 其他应付款），
    // 把结果摆到明面上，老板不用打开凭证也能确认科目符合预期
    const summary = voucherLinesSummary(res?.entry?.voucherLines)
    message.success(summary ? `已确认：${choice.label}（${summary}）` : `已确认：${choice.label}`)
    clearDispositionState(id)
    await reloadAll()
  } catch (err: any) {
    message.error(apiErrorMessage(err, '确认失败'))
  } finally {
    dispositionSubmittingId.value = null
  }
}

function cancelDisposition(entry: any) {
  clearDispositionState(String(entry.id))
}

/** 撤销去向确认：单据重新回到待确认列表 */
async function resetEntryDisposition(entry: any) {
  const id = String(entry?.id || '')
  if (!id || dispositionResettingId.value) return
  if (!canResetEntryDisposition(entry)) {
    message.warning(
      isReversalLikeEntry(entry)
        ? '冲销凭证不能重新选择'
        : '已过账记录请先撤销对应核销或按凭证冲销流程处理',
    )
    return
  }
  dispositionResettingId.value = id
  try {
    await updateEntry(entry.id, { paymentStatus: null, payer: 'company_account' })
    message.success('已恢复为待确认状态')
    await reloadAll()
  } catch (err: any) {
    message.error(apiErrorMessage(err, '恢复失败'))
  } finally {
    dispositionResettingId.value = null
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
  '个税代缴',
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

const aiSuggestedMatchedEntryIds = computed(() =>
  collectSuggestedMatchedEntryIds(
    aiSuggestions.value,
    new Set(unmatchedStatements.value.map((stmt: any) => String(stmt.id))),
  )
)

const allPendingEntryCandidates = computed(() =>
  unmatchedEntries.value.filter((entry: any) => {
    const isInvoice = entry.source === 'invoice_import' || !!entry.invoiceNumber
    if (isInvoice) return true
    // 银行导入的账目本身就是公户流水，去向已确定，应走「待处理银行流水」的关联疑似流水，
    // 不需要在这里再问谁付的钱（否则误选老板垫付会把公户付款改记成股东垫付，流水再认领会重复入账）
    const isBankImport = entry.source === 'bank_import' || String(entry.id || '').startsWith('bank-')
    if (isBankImport) return false
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

const pendingEntryList = computed(() =>
  allPendingEntryCandidates.value.filter(
    (entry: any) => !aiSuggestedMatchedEntryIds.value.has(String(entry.id))
  )
)

const bankMatchedPendingEntriesCount = computed(() =>
  allPendingEntryCandidates.value.length - pendingEntryList.value.length
)

/** 资金流出：支出，或负数的资本往来（股东取现等）；股东投入按流入显示 */
const entryIsOutflow = (entry: any) =>
  entry.direction === 'expense' || (entry.direction !== 'income' && Number(entry.amount) < 0)

// ── 前期已付款（先付款后收票）───────────────────────────────────────────────

/** 批量拉取待确认发票的前期付款候选（只读；失败不打断结账，四个常规选项仍可用）。 */
async function loadPriorPaymentCandidates() {
  const invoiceEntryIds = pendingEntryList.value
    .filter((entry: any) => entry.source === 'invoice_import' || !!entry.invoiceNumber)
    .map((entry: any) => String(entry.id))
  if (!invoiceEntryIds.length) {
    priorPaidCandidates.value = {}
    return
  }
  priorPaidCandidatesLoading.value = true
  try {
    const res = await fetchPriorPaymentCandidates(invoiceEntryIds)
    priorPaidCandidates.value = res.candidates || {}
  } catch (err: any) {
    console.error('[loadPriorPaymentCandidates] 前期付款检测失败:', err)
    priorPaidCandidates.value = {}
  } finally {
    priorPaidCandidatesLoading.value = false
  }
}

/** 强置信候选：卡片上直接给建议，不必展开列表挑。 */
function priorPaidSuggestionOf(entry: any): PriorPaymentCandidate | null {
  // 当月已有疑似流水可关联（「关联疑似流水」是首选路径）：
  // 不叠加前期建议，否则两条提示打架，还容易把无关的前期凭证误推上来
  if (findStatementCandidateForEntry(entry)) return null
  const list = priorPaidCandidates.value[String(entry.id)] || []
  // 只有「流水对方含销方名」的强匹配才自动建议；
  // text/amount_only 交给「前期已付款」列表人工挑，避免通用词误报
  return list.find((c) => c.confidence === 'counterparty') || null
}

/** 全部候选（含弱匹配）：走「前期已付款…」按钮展开挑选。 */
function priorPaidListFor(entry: any): PriorPaymentCandidate[] {
  return priorPaidCandidates.value[String(entry.id)] || []
}

function priorPaidCandidateTitle(candidate: PriorPaymentCandidate): string {
  const day = String(candidate.occurredOn || '').slice(0, 10)
  return `${day}「${candidate.summary}」`
}

async function executeLinkToPriorPayment(entry: any, candidate: PriorPaymentCandidate) {
  const id = String(entry.id)
  priorPaidLinkingId.value = id
  try {
    await linkPriorPayment(id, candidate.entryId)
    message.success(`已挂接到 ${priorPaidCandidateTitle(candidate)}，本月不再重复入账`)
    expandedPriorPaidEntryId.value = null
    clearDispositionState(id)
    await reloadAll()
  } catch (err: any) {
    message.error(apiErrorMessage(err, '挂接失败'))
  } finally {
    priorPaidLinkingId.value = null
  }
}

/**
 * 确认挂接：发票主数据迁到前期凭证 + 删除本月草稿（后端同一事务完成）。
 * 挂接后 entry 消失，不再出现在待确认列表，本月锁账不会重复入账。
 */
function linkToPriorPayment(entry: any, candidate: PriorPaymentCandidate) {
  dialog.warning({
    title: '挂接到前期已入账凭证？',
    content: `这笔款项已于 ${priorPaidCandidateTitle(candidate)} 入账。挂接后本月这张发票不再生成新费用凭证（避免与前期重复入账），发票作为该凭证的付款证据留存。`,
    positiveText: '确认挂接',
    negativeText: '先不挂',
    onPositiveClick: () => executeLinkToPriorPayment(entry, candidate),
  })
}


function mapExceptionEntryRow(e: any) {
  return {
    id: e.id,
    title: e.title,
    date: e.occurDate,
    amount: e.amount,
    direction: e.direction,
    category: e.category || '',
    source: e.source || 'manual',
    payer: e.payer || 'company_account',
    paymentStatus: e.paymentStatus || '',
    billingStatus: e.billingStatus || e.billing_status || '',
    paymentDate: e.paymentDate || e.payment_date || '',
    invoiceNumber: e.invoiceNumber || '',
    status: e.status || '',
    voucherStatus: e.voucherStatus || '',
    entryType: e.entryType || e.entry_type || '',
    reversalOf: e.reversalOf || '',
  }
}

const confirmedExceptionEntries = computed(() => {
  try {
    return entryStore.activeEntries.filter((e) => {
      const month = e.occurDate ? e.occurDate.substring(0, 7) : ''
      const isCurrentMonth = month === targetMonth.value
      const isNotArchiveOnly = !((e as any).is_archive_only || (e as any).isArchiveOnly)
      return (
        isCurrentMonth
        && isNotArchiveOnly
        && !isReversalLikeEntry(e as any)
        && isNonBankSettledEntry(e as any)
      )
    }).map(mapExceptionEntryRow)
  } catch (err) {
    console.error('confirmedExceptionEntries computed error:', err)
    return []
  }
})

/** 截至目标月份仍未报销的个人垫付单据明细（含往期未报与当期草稿），供往来抽屉穿透查看。 */
const unreimbursedAdvanceEntries = computed(() => {
  try {
    const rows = entryStore.activeEntries.filter((e) => {
      const month = e.occurDate ? e.occurDate.substring(0, 7) : ''
      return Boolean(month && month <= targetMonth.value)
        && isUnreimbursedPersonalAdvanceEntry(e as any, targetMonth.value)
    })
    return sortEntriesByDateDescending(rows).map(mapExceptionEntryRow)
  } catch (err) {
    console.error('unreimbursedAdvanceEntries computed error:', err)
    return []
  }
})

/** 老板往来小账本汇总：让老板一眼看懂确认后的应收、应付、可免税报销垫付款 */
const bossLedgerSummary = computed(() =>
  computeBossLedgerSummary(confirmedExceptionEntries.value, advanceBalances.value, targetMonth.value)
)

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
    lastConfirmedAction.value = {
      label: `「${invoice.title}」关联流水「${cleanStatementTitle(stmt) || stmt.description}」`,
      items: [{ statementId: stmt.id, type: 'match', title: invoice.title }],
    }
    message.success(`已关联发票并完成核对：「${invoice.title}」`)
    await reloadAll()
  } catch (err: any) {
    if (isBackfillCancelled(err)) return
    message.error(apiErrorMessage(err, '发票关联失败'))
  } finally {
    quickSubmittingId.value = null
  }
}

const multiMatchBasePool = computed(() => {
  try {
    const matchedIds = new Set(rawStatements.value.flatMap((statement) => getStatementMatchedEntryIds(statement)))
    const reversalIds = new Set(entryStore.activeEntries
      .filter((e) => (e as any).entryType === 'reversal' || (e as any).entry_type === 'reversal')
      .map((e) => String((e as any).reversalOf || '').trim())
      .filter(Boolean))

    const rows = entryStore.activeEntries.filter((e: any) => {
      const entryMonth = e.occurDate ? String(e.occurDate).slice(0, 7) : ''
      if (!entryMonth || entryMonth > targetMonth.value) return false
      if (matchedIds.has(e.id)) return false
      if (e.is_archive_only || e.isArchiveOnly) return false
      if (isReconciledEntry(e)) return false
      if (isReversalLikeEntry(e) || reversalIds.has(String(e.id))) return false

      const payer = String(e.payer || '')
      const paymentStatus = String(e.paymentStatus || '')
      if (paymentStatus === 'unpaid') return false
      if (['prepaid_deduct', 'platform_balance', 'advance_offset'].includes(payer)) return false

      const poolInfo = classifyMultiMatchCandidate(e)
      if (!poolInfo) return false
      if (poolInfo.poolKind !== 'advancement' && isNonBankSettledEntry(e)) return false
      return true
    })

    return sortEntriesByDateDescending(rows).map((e: any) => ({
      id: e.id,
      title: e.title || '',
      date: e.occurDate || '',
      occurDate: e.occurDate || '',
      amount: Math.abs(Number(e.amount) || 0),
      direction: e.direction || 'expense',
      category: e.category || '',
      source: e.source || 'manual',
      payer: e.payer || 'company_account',
      paymentStatus: e.paymentStatus || '',
      voucherLines: e.voucherLines || null,
      poolInfo: classifyMultiMatchCandidate(e),
    }))
  } catch (err) {
    console.error('multiMatchBasePool computed error:', err)
    return []
  }
})

function getMultiMatchCandidatesForStatement(stmt: any): any[] {
  if (!stmt) return []
  const stmtDate = String(stmt.transaction_date || '').slice(0, 10)
  const isInflow = Number(stmt.amount) >= 0
  return multiMatchBasePool.value.filter((item: any) => {
    if (!isDirectionCompatible(item.direction, stmt.amount)) return false
    if (isInflow && item.poolInfo?.poolKind === 'advancement') return false
    const entryDate = String(item.occurDate || '').slice(0, 10)
    if (stmtDate && entryDate && entryDate > stmtDate) return false
    return true
  })
}

async function confirmMultiMatch(stmt: any, entryIds: string[]): Promise<boolean> {
  if (!stmt || !entryIds.length) return false
  quickSubmittingId.value = stmt.id
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
    lastConfirmedAction.value = {
      label: `「${cleanStatementTitle(stmt) || stmt.description}」合并核销 ${entryIds.length} 笔单据`,
      items: [{ statementId: stmt.id, type: 'match', title: `合并核销 ${entryIds.length} 笔` }],
    }
    message.success(`已完成合并核销（共 ${entryIds.length} 笔单据）`)
    await reloadAll()
    return true
  } catch (err: any) {
    if (!isBackfillCancelled(err)) {
      message.error(apiErrorMessage(err, '合并核销失败'))
    }
    return false
  } finally {
    quickSubmittingId.value = null
  }
}

const bossExpenseOptions = [
  { label: '办公耗材 / 打印杂费', value: '办公费' },
  { label: '信息服务 / 云服务器 / 软件', value: '信息技术服务费' },
  { label: '请客吃饭 / 商务招待', value: '业务招待费' },
  { label: '打车交通 / 差旅住宿', value: '差旅费' },
  { label: '研发项目支出（需补项目 →）', value: '研发支出-费用化支出' },
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
  persistedMatchedPairs,
  aiSuggestions,
  aiSuggestionsLoading,
  reconciliationHealth,
  reconciliationHealthLoading,
  filterUnmatchedData,
  suggestionsController,
  loadAiSuggestions,
  emptyReconciliationHealth,
  loadReconciliationHealth,
  deletePair,
  matchUndoableEntries,
  undoMatchEntry,
  isDirectionCompatible,
  safeFloat,
  isItemAutoMatched,
  monthInvoiceRows,
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
  renderCategoryLabel,
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
  manualAdvancedOpen,
  manualSuggestionMeta,
  showPairCategoryDialog,
  pairCategoryTarget,
  pairCategoryForm,
  pairCategorySubmitting,
  showEditVoucherDialog,
  editVoucherForm,
  editVoucherSaving,
  editVoucherTargetEntry,
  editVoucherBatchSiblings,
  editVoucherApplyToSiblings,
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
  findRefundPairPartner,
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
  openPendingEntryEdit,
  setEditVoucherSettlement,
  editableMatchedEntryOf,
  saveEditVoucher,
  refreshManualAiSuggestion,
  handleManualCategoryChange,
  handleManualClassify,
  highConfidenceStatements,
  statementTierFilter,
  selectedStatementIds,
  rememberRuleOnConfirm,
  lastConfirmedAction,
  undoingLastConfirm,
  undoLastConfirmed,
  dismissLastConfirmed,
  getPayrollSettlement,
  advancePayableOf,
  advancePayableSourceText,
  invoiceReimbursementOf,
  fallbackMatchedEntriesOf,
  matchedFallbackCategoryOf,
  handleAdvanceAsNewExpense,
  isPayrollSettlementSuggestion,
  isSelectableForBatch,
  isNeedsAttentionStatement,
  suggestionBadgeLabel,
  selectableBatchStatements,
  readyStatements,
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
  handlePayrollSettlementAction,
  handleAcceptSuggested,
  handleQuickSelectCategory,
  findMatchingInvoiceForStatement,
  findStatementCandidateForEntry,
  statementCandidateText,
  handleEntryPaid,
  beginDisposition,
  acceptPendingDisposition,
  cancelDisposition,
  setDispositionCategory,
  dispositionRecommendedValue,
  dispositionRecommendationReason,
  dispositionPreviewOf,
  dispositionPreviewDebitLines,
  dispositionPreviewCreditLines,
  dispositionCategorySelectGroups,
  expandedDispositionEntryId,
  dispositionChoice,
  dispositionRecommendation,
  dispositionCategorySelected,
  dispositionPreview,
  dispositionSubmittingId,
  dispositionCategoryGroups,
  dispositionRdChoice,
  dispositionRdEnabled,
  dispositionRdMissing,
  rdChoiceOf,
  setDispositionRdField,
  rdCategoryEnabled,
  effectiveRdProjectOptions,
  effectiveRdProjects,
  editVoucherBusinessContext,
  editVoucherAiLoading,
  editVoucherAiSuggestion,
  editVoucherPreview,
  refreshEditVoucherAi,
  loadEditVoucherPreview,
  resetEntryDisposition,
  canResetEntryDisposition,
  dispositionResettingId,
  employeePayerOptions,
  SYSTEM_MANUAL_CATEGORIES,
  pendingEntryList,
  bankMatchedPendingEntriesCount,
  entryIsOutflow,
  confirmedExceptionEntries,
  unreimbursedAdvanceEntries,
  advanceBalances,
  loadAdvanceBalances,
  bossLedgerSummary,
  pendingInvoiceList,
  unpaidInvoiceCount,
  handleMatchInvoiceDirect,
  getMultiMatchCandidatesForStatement,
  confirmMultiMatch,
  bossExpenseOptions,
  bossIncomeOptions,
  priorPaidCandidates,
  priorPaidCandidatesLoading,
  priorPaidLinkingId,
  expandedPriorPaidEntryId,
  loadPriorPaymentCandidates,
  priorPaidSuggestionOf,
  priorPaidListFor,
  priorPaidCandidateTitle,
  linkToPriorPayment,
  }
}
