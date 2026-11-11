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
import { previewEntryVoucher, updateEntry } from '@/api/record'
import { reverseEntry } from '@/api/accounting'
import { accountingErrorText } from '@/utils/errors'
import { formatMoney } from '@/utils/format'
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
// 真正持久化的配对（流水 matched_entry_id 已落库）。建议对（persisted=false，
// 待用户点确认配对）不得混进"已认领并完成"列表——否则用户点撤销只是本地
// 忽略，刷新后 ai-suggest 重新建议，看起来像"撤销无效又回来了"。
const persistedMatchedPairs = computed(() => matchedPairs.value.filter((p: any) => p.persisted))
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
    } catch (err) {
      message.error(accountingErrorText(err))
      return
    }
    // 取消关联只解链不删原凭证（仅微信结算凭证会被后端一并删除）。不说明这一点，
    // 用户会困惑"刚认可入账的流水怎么又变回未入账"，再次认可入账还会撞防重复弹窗
    const originalIds: string[] = pair.matchedEntryIds || [pair.matchedEntryId].filter(Boolean)
    const entriesById = new Map((pair.entries || []).map((e: any) => [String(e.id), e]))
    const hadSettlement = originalIds.some((entryId) => String(entryId).startsWith('bank-settlement-'))
    // 认可入账创建的凭证（source=bank_import）支持一键"撤销入账"：
    // 草稿删除、已过账红冲，之后该流水回到全新待处理状态（换分类/详细记账全可用）。
    // 发票/工资等其他来源的凭证不动，只解链。
    const bankCreated: any[] = originalIds
      .map((entryId) => entriesById.get(String(entryId)))
      .filter((e: any) => e && String(e.source || '') === 'bank_import')

    if (!bankCreated.length) {
      message.info(
        hadSettlement
          ? '对账关联已取消；原账目仍保留在账上，微信结算凭证已一并删除'
          : '对账关联已取消；原凭证仍保留在账上，可重新配对，或到凭证页删除',
      )
      await reloadAll()
      return
    }

    dialog.warning({
      title: '对账关联已取消',
      content: `原入账凭证「${bankCreated[0].title || '未命名'}」${bankCreated.length > 1 ? `等 ${bankCreated.length} 笔` : ''}仍在账上。撤销入账后该流水回到待处理，可重新选择分类入账；保留则可重新配对。`,
      positiveText: '撤销原入账，重新选择分类',
      negativeText: '保留在账上',
      onPositiveClick: async () => {
        const failed = await removeBankCreatedEntries(bankCreated)
        if (failed.length) {
          message.warning(`部分凭证未能撤销（已过账请到账目页冲销）：${failed.join('、')}`)
        } else {
          message.success('原入账已撤销，该流水已回到待处理，可重新选择分类入账')
        }
        await reloadAll()
      },
      onNegativeClick: async () => {
        await reloadAll()
      },
    })
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
  { label: '住房公积金', value: 'expense|社保公积金|221105|应付职工薪酬-住房公积金' },
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
// 改凭证弹窗的当前目标：过账弹层走 currentVoucherEntry，待确认卡片直接传 entry
const editVoucherTargetEntry = ref<any>(null)
// 「详细记账」：业务说明 → AI 重推分类；科目预览与第二步同一口径
const editVoucherBusinessContext = ref('')
const editVoucherAiLoading = ref(false)
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
  openPendingEntryEdit(entry)
}

// 待确认单据卡片的「修改/详细记账」入口：与过账弹层共用改凭证弹窗与保存逻辑
function openPendingEntryEdit(entry: any) {
  if (!entry) return
  editVoucherTargetEntry.value = entry
  const rdDetail = rdDetailFromEntry(entry)
  // 从第二步进入时带上正在看的分类（含 AI 建议），弹窗与卡片同口径
  const pendingCategoryValue = dispositionCategorySelected.value[String(entry.id)]
  editVoucherForm.value = {
    title: entry.title || '',
    categoryValue: pendingCategoryValue || categoryValueFromEntry(entry, null),
    rdProjectId: rdDetail?.projectId || '',
    rdExpenseType: rdDetail?.expenseType || '',
    rdAllocationBasis: rdDetail?.allocationBasis || '',
  }
  editVoucherBusinessContext.value = ''
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
    const preview = await previewEntryVoucher(
      String(entry.id),
      dispositionPayload(dispositionChoice.value[String(entry.id)], category, rdFields),
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
    const value = dispositionCategoryValue(
      entry.direction === 'income' ? 'income' : 'expense',
      String(res?.category || ''),
    )
    if (!value) {
      message.warning('AI 没给出可用的科目建议，请直接选择分类')
      return
    }
    editVoucherForm.value = { ...editVoucherForm.value, categoryValue: value }
    message.success(
      res?.reason ? `AI 建议：${res.category}（${res.reason}）` : `AI 建议：${res.category}`,
    )
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
    // 发票挂账语义保护：invoice_import 且未设置付款状态的凭证，重算分录时
    // 贷方必须保持 2241 挂账（paymentStatus=unpaid），不能随 company_account
    // 默认回落成贷 1002"银行直付"——那正是历史上"公户没扣款却挂贷银行"的来源。
    // 第二步已选了付款方式时以用户选择为准，不再套这层兜底。
    const isInvoiceEntry =
      String(entry.source || '') === 'invoice_import' || !!entry.invoiceNumber
    const payer = String(disposition?.payer || entry.payer || 'company_account')
    const invoicePendingGuard =
      !disposition && !isPosted && isInvoiceEntry && payer === 'company_account' && !entry.paymentStatus
        ? { paymentStatus: 'unpaid' as const }
        : {}
    const payload: Record<string, any> = {
      category,
      ...dispositionPayload(disposition),
      ...invoicePendingGuard,
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
    await entryStore.fetchEntries()
    showEditVoucherDialog.value = false
    editVoucherTargetEntry.value = null
    if (disposition) clearDispositionState(targetId)
    const summary = voucherLinesSummary(res?.entry?.voucherLines)
    message.success(summary ? `已更新：${summary}` : `已更新分类：${category}`)
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
    return sug && !sug.requiresReview && sug.confidence >= 0.95
  })
})

const statementTierFilter = ref<'all' | 'ready' | 'attention'>('all')
const selectedStatementIds = ref<string[]>([])
const rememberRuleOnConfirm = ref(true)

function isSelectableForBatch(stmt: any): boolean {
  const sug = getAiSuggestion(stmt.id)
  if (!sug) return false
  if (sug.requiresReview) return false
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
  return isFallbackSuggestion(stmt) || Boolean(sug.requiresReview)
}

function suggestionBadgeLabel(stmt: any): string {
  if (isFallbackSuggestion(stmt)) return '待人工指定'
  const sug = getAiSuggestion(stmt.id)
  if (!sug) return '待指定'
  if (sug.requiresReview) return '规则待复核'
  // 规则类建议展示真实置信度：写死 100% 会误导用户以为行已被完全决断，
  // 而低置信(<0.95)的模糊行勾选框其实被禁用、需点候选 chip 逐笔确认
  const ruleConf = `${Math.round(Number(sug.confidence || 0) * 100)}%`
  if (sug.rule === 'company_memory_rule') return `记忆规则 ${ruleConf}`
  if (sug.rule) return `系统规则 ${ruleConf}`
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
function shouldRememberSuggestion(sug: any): boolean {
  const category = String(sug?.suggestedEntry?.category || '')
  return Boolean(
    rememberRuleOnConfirm.value
    && sug?.type === 'new'
    && sug.rule !== 'refund_pair'
    && !sug.requiresReview
    && !getAmbiguityGroup(category).length
  )
}

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
        const rememberRule = shouldRememberSuggestion(sug)
        const res: any = await confirmSuggested(stmt, sug, { rememberRule })
        if (res?.ruleWarning) message.warning(res.ruleWarning)
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
  // 模糊组、退款配对和需复核建议都不能沉淀为自动规则。
  const shouldRemember = Boolean(
    shouldRememberSuggestion(sug)
    && !isFallbackSuggestion(stmt)
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
    if (res?.ruleWarning) message.warning(res.ruleWarning)
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

/**
 * 第一步：记录资金来源选择并就地展开第二步（不落库）。
 * 只有点「认可入账」才发 PATCH——否则单据会立刻被当成已确认、从待确认列表消失。
 */
function beginDisposition(entry: any, choice: DispositionChoice) {
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
  const warnings = dispositionPreview.value[id]?.preview?.warnings || []
  let confirmRegenerate = false
  if (warnings.length) {
    confirmRegenerate = await new Promise<boolean>((resolve) => {
      dialog.warning({
        title: '分录将被重新生成',
        content: warnings.map((item) => item.message).join('；'),
        positiveText: '继续入账',
        negativeText: '取消',
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
        onClose: () => resolve(false),
        onMaskClick: () => resolve(false),
      })
    })
    if (!confirmRegenerate) return
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

/** 资金流出：支出，或负数的资本往来（股东取现等）；股东投入按流入显示 */
const entryIsOutflow = (entry: any) =>
  entry.direction === 'expense' || (entry.direction !== 'income' && Number(entry.amount) < 0)

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
      status: (e as any).status || '',
      voucherStatus: (e as any).voucherStatus || '',
      entryType: (e as any).entryType || (e as any).entry_type || '',
      reversalOf: (e as any).reversalOf || '',
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
  manualSuggestionMeta,
  showPairCategoryDialog,
  pairCategoryTarget,
  pairCategoryForm,
  pairCategorySubmitting,
  showEditVoucherDialog,
  editVoucherForm,
  editVoucherSaving,
  editVoucherTargetEntry,
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
  openPendingEntryEdit,
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
  editVoucherPreview,
  refreshEditVoucherAi,
  loadEditVoucherPreview,
  resetEntryDisposition,
  canResetEntryDisposition,
  dispositionResettingId,
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
