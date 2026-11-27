/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export type CloseCheckStatus = 'complete' | 'blocked' | 'needs_review' | 'not_required' | 'incomplete'

export interface PeriodCloseCheck {
  code: string
  name: string
  status: string
  uiStatus?: CloseCheckStatus
  [key: string]: unknown
}

export interface ClosingFinding {
  findingId: string
  checkCode: string
  severity: 'blocking' | 'warning' | 'info'
  kind: string
  title: string
  count: number
  amount: string | null
  authoritativeIds: {
    statementIds: string[]
    entryIds: string[]
    assetIds: string[]
    scheduleIds: string[]
  }
  allowedRepairs: string[]
  details?: unknown
}

export interface PeriodCloseChecks {
  schemaVersion?: 'closing.v1'
  period?: string
  periodStatus?: string
  canClose?: boolean
  blockingCount?: number
  checks?: PeriodCloseCheck[]
  notices?: ClosingNotice[]
  noticeCount?: number
  snapshotHash?: string
  generatedAt?: string
  periodVersion?: number
  ruleVersions?: Record<string, string>
  findings?: ClosingFinding[]
  findingsTotal?: number
  nextFindingCursor?: number | null
}

/**
 * 提示项：回答"有没有业务被漏在别的月份"，与五项核对（"账做平了没有"）分开。
 * 不参与 canClose，只要求用户知情。
 */
export interface ClosingNoticeRecord {
  businessDate?: string
  businessPeriod?: string
  originalPeriod?: string
  postingPeriod?: string
  description?: string
  summary?: string
  amount?: string
  voucherRef?: string
  reason?: string
  sourceType?: string
  entryId?: string
  /** 增值税申报期差异（VAT_PERIOD_MISMATCH）专用字段 */
  vatAmount?: string
  businessFilingPeriod?: string
  postingFilingPeriod?: string
}

export interface ClosingNotice {
  code: string
  name: string
  severity: 'warning' | 'info' | 'success'
  count: number
  amount?: string
  message?: string
  suggestedAction?: string
  /** 工资超期未发放时，最早一期欠发工资所属月份（结账提示深链用） */
  suggestedMonth?: string
  records?: ClosingNoticeRecord[]
}

export interface ClosingReadinessItem {
  code: string
  title: string
  status: CloseCheckStatus
  detail: string
  actionLabel: string
  step: number
  route?: string
  waitingForPosting?: boolean
}

const COPY: Record<string, Omit<ClosingReadinessItem, 'status'>> = {
  PERIOD_ORDER: {
    code: 'PERIOD_ORDER', title: '关账顺序', detail: '更早的月份都已关账。', actionLabel: '去结更早的月份', step: 3,
  },
  VOUCHER_TO_SOURCE: {
    code: 'VOUCHER_TO_SOURCE', title: '凭证与来源检查', detail: '凭证已过账，且关联有效的原始业务记录。', actionLabel: '查看本月凭证', step: 1, route: '/vouchers',
  },
  LEDGER_TO_LEDGER: {
    code: 'LEDGER_TO_LEDGER', title: '账目内部一致（账账核对）', detail: '确认借方和贷方金额一致。', actionLabel: '查看核对明细', step: 4,
  },
  BOOK_TO_ACTUAL: {
    code: 'BOOK_TO_ACTUAL', title: '银行流水与账目配对（银行核对）', detail: '确认账面记录与实际资金流水一致。', actionLabel: '去核对', step: 4,
  },
  LEDGER_TO_REPORT: {
    code: 'LEDGER_TO_REPORT', title: '报表与账目一致（账表核对）', detail: '核对申报财务报表平衡、现金关系及账簿货币资金汇总。', actionLabel: '查看报表', step: 4, route: '/reports',
  },
  REPORT_TO_TAX: {
    code: 'REPORT_TO_TAX', title: '申报财务报表校验', detail: '检查记账规则及申报财务报表内部勾稽；不代表已完成税务申报。', actionLabel: '查看本月凭证', step: 1, route: '/vouchers',
  },
  INTANGIBLE_AMORTIZATION: {
    code: 'INTANGIBLE_AMORTIZATION', title: '无形资产摊销', detail: '本月无形资产摊销已计提入账。', actionLabel: '去摊销', step: 4, route: '/assets?tab=intangible',
  },
  FIXED_ASSET_DEPRECIATION: {
    code: 'FIXED_ASSET_DEPRECIATION', title: '固定资产折旧', detail: '固定资产折旧已按期间计提入账', actionLabel: '去计提折旧', step: 4, route: '/assets',
  },
  IMPAIRMENT_TEST_DUE: {
    code: 'IMPAIRMENT_TEST_DUE', title: '无形资产减值迹象', detail: '存在未复核的减值迹象记录，请评估。', actionLabel: '去评估', step: 4, route: '/assets?tab=intangible',
  },
  PAYROLL_ACCRUAL: {
    code: 'PAYROLL_ACCRUAL', title: '工资计提', detail: '本月工资已按权责发生制计提，发放可延至次月。', actionLabel: '去计提工资', step: 2, route: '/mine/salary',
  },
  PLATFORM_CLEARING_BALANCE: {
    code: 'PLATFORM_CLEARING_BALANCE', title: '平台待结算核对', detail: '微信、支付宝待结算余额正常。', actionLabel: '去导入账单', step: 1,
  },
}

function normalizeStatus(check: PeriodCloseCheck): CloseCheckStatus {
  if (check.uiStatus) return check.uiStatus
  if (check.status === 'passed') return 'complete'
  if (check.status === 'blocking') return 'blocked'
  if (check.status === 'needs_review') return 'needs_review'
  if (check.status === 'not_required') return 'not_required'
  return 'incomplete'
}

function detailFor(check: PeriodCloseCheck, fallback: string): string {
  if (check.code === 'PERIOD_ORDER') {
    const pending = Number(check.earlierOpenCount || 0)
    if (pending) {
      const earliest = String(check.earliestOpenMonth || '')
      if (!earliest) return `有 ${pending} 个更早的月份还没结账，请从最早的月份开始。`
      return pending > 1
        ? `${earliest} 等 ${pending} 个更早的月份还没结账，请先补结。`
        : `${earliest} 还没结账，请先结完 ${earliest} 再结本月。`
    }
  }
  if (check.code === 'VOUCHER_TO_SOURCE') {
    const draft = Number(check.draftCount ?? check.pendingCount ?? 0)
    const review = Number(check.pendingReviewCount || 0)
    const orphan = Number(check.orphanVoucherCount || 0)
    const reasons = [draft ? `${draft} 笔待过账` : '', review ? `${review} 笔待复核` : '', orphan ? `${orphan} 笔缺少有效来源` : ''].filter(Boolean)
    if (reasons.length) return `${reasons.join('；')}。`
  }
  if (check.blockedByPendingCount && check.status !== 'blocking') return `尚有 ${check.blockedByPendingCount} 笔凭证未过账，完成后重新核验。`
  if (check.code === 'BOOK_TO_ACTUAL') {
    const pending = Number(check.unreconciledBankCount || 0)
    const issues = check.issues as Array<{ reason?: string }> | undefined
    if (issues?.length) return `${issues.length} 项银行核对问题：${issues[0]?.reason || '请查看对账明细'}。`
    if (pending) return `${pending} 笔银行流水还未核对。`
  }
  if (check.code === 'REPORT_TO_TAX') {
    const pending = Number(check.missingRuleVersionCount || 0)
    if (pending) return `${pending} 笔记录缺少本期记账规则。`
  }
  if (check.code === 'INTANGIBLE_AMORTIZATION') {
    const pending = Number(check.pendingCount || 0)
    if (pending) return `有 ${pending} 项无形资产未计提摊销，合计 ${check.totalAmount ?? ''} 元。`
  }
  if (check.code === 'FIXED_ASSET_DEPRECIATION') {
    const pending = Number(check.pendingCount || 0)
    if (pending) return `有 ${pending} 期固定资产折旧未计提，合计 ${check.totalAmount ?? ''} 元。`
  }
  if (check.code === 'IMPAIRMENT_TEST_DUE') {
    const count = Number(check.count || 0)
    if (count) return `${count} 条减值迹象记录待复核。`
  }
  if (check.code === 'PAYROLL_ACCRUAL') {
    const message = String(check.message || '')
    if (message) return `${message}。`
  }
  if (check.code === 'PLATFORM_CLEARING_BALANCE') {
    const message = String(check.message || '')
    if (message) return message.endsWith('。') ? message : `${message}。`
  }
  const issues = check.issues as Array<{ message?: string }> | undefined
  if (issues?.length) return issues.map(issue => issue.message || '存在报表差异').join('；')
  return fallback
}

// ponytail: checks if all remaining unposted vouchers are clean drafts that will be auto-posted by backend close_period in a single transaction
export function canBatchPostDrafts(checksData: PeriodCloseChecks | null | undefined): boolean {
  const checks = checksData?.checks
  if (!checks || !Array.isArray(checks) || checks.length === 0) return false
  const vouchers = checks.find(c => c.code === 'VOUCHER_TO_SOURCE')
  if (!vouchers) return false
  const autoPostCount = Number(vouchers.autoPostAtCloseCount ?? vouchers.draftCount ?? 0)
  if (autoPostCount === 0) return false
  if (Number(vouchers.pendingReviewCount || 0) > 0 || Number(vouchers.orphanVoucherCount || 0) > 0) return false

  const draftIds = new Set(
    ((vouchers.records as any[]) || [])
      .filter((r: any) => r.reasons?.includes('pending_post') && !r.reasons?.includes('missing_source'))
      .map((r: any) => String(r.entryId))
  )
  if (draftIds.size !== autoPostCount) return false

  return checks.every(check => {
    if (check.code === 'VOUCHER_TO_SOURCE') return true
    if (check.status === 'passed' || (check.status === 'needs_review' && Number(check.blockedByPendingCount) > 0)) return true
    if (check.code === 'BOOK_TO_ACTUAL' && Number(check.unreconciledBankCount || 0) === 0) {
      const issues = (check.issues as any[]) || []
      const projDiff = (check.balanceReconciliation as any)?.projected_book_difference
      const projBalanced = projDiff === undefined || Math.abs(Number(projDiff)) <= 0.01
      return issues.length === 0 || issues.every(issue =>
        (issue.type === 'unposted_match' && draftIds.has(String(issue.entryId)))
        || (issue.type === 'bank_balance_mismatch' && projBalanced)
      )
    }
    return false
  })
}

export function getClosingReadiness(checks: PeriodCloseChecks | null | undefined): ClosingReadinessItem[] {
  const batchPostable = canBatchPostDrafts(checks)
  const voucherCheck = (checks?.checks || []).find(c => c.code === 'VOUCHER_TO_SOURCE')
  const voucherUnposted = Number(voucherCheck?.autoPostAtCloseCount ?? voucherCheck?.draftCount ?? 0)
    + Number(voucherCheck?.pendingReviewCount || 0)

  return (checks?.checks || []).map((check) => {
    const copy = COPY[check.code] || {
      code: check.code,
      title: check.name || check.code,
      detail: '查看这项结账检查的结果。',
      actionLabel: '查看详情',
      step: 4,
    }
    let status = normalizeStatus(check)
    let detail = detailFor(check, copy.detail)
    let waitingForPosting = false

    if (batchPostable) {
      if (check.code === 'VOUCHER_TO_SOURCE') {
        const count = Number(check.autoPostAtCloseCount ?? check.draftCount ?? 0)
        status = 'complete'
        detail = `本月有 ${count} 笔草稿凭证将在关账时自动编号过账。`
      } else if (status === 'needs_review' || (status === 'blocked' && check.code === 'BOOK_TO_ACTUAL' && Number(check.unreconciledBankCount || 0) === 0)) {
        status = 'complete'
        detail = '已就绪（将在结账自动过账后一并核验归档）。'
      }
    } else if (voucherUnposted > 0) {
      // 凭证尚未过账时，仅因未过账产生的下游挂起/余额差异归为「待过账后核验」，避免误报成真正错账。
      if (check.code === 'BOOK_TO_ACTUAL' && Number(check.unreconciledBankCount || 0) === 0) {
        const issues = (check.issues as any[]) || []
        if (
          issues.length > 0
          && issues.every(issue => issue.type === 'unposted_match' || issue.type === 'bank_balance_mismatch')
        ) {
          status = 'incomplete'
          detail = `尚有 ${voucherUnposted} 笔凭证未过账，过账后将自动完成银行流水与余额核对。`
          waitingForPosting = true
        }
      } else if (status === 'incomplete' && Number(check.blockedByPendingCount || 0) > 0) {
        waitingForPosting = true
      }
    }

    return {
      ...copy,
      status,
      detail,
      ...(waitingForPosting ? { waitingForPosting: true } : {}),
    }
  })
}

export function firstActionableReadinessItem(items: ClosingReadinessItem[]): ClosingReadinessItem | undefined {
  return items.find((item) => ['blocked', 'needs_review', 'incomplete'].includes(item.status))
}

// ── 五项核对常显条 ──────────────────────────────────────────────
// 结账检查里最核心的五项，按「账证 → 账账 → 账实 → 账表 → 申报」固定顺序展示。
// 其余准备项（折旧、摊销、工资、关账顺序）不进入这条，避免把五项扩成十项。

export const FIVE_CLOSING_CHECKS = [
  { code: 'VOUCHER_TO_SOURCE', label: '凭证与来源' },
  { code: 'LEDGER_TO_LEDGER', label: '账账核对' },
  { code: 'BOOK_TO_ACTUAL', label: '银行流水核对' },
  { code: 'LEDGER_TO_REPORT', label: '账表核对' },
  { code: 'REPORT_TO_TAX', label: '申报校验' },
] as const

export interface FiveCheckSummaryItem {
  code: string
  label: string
  status: CloseCheckStatus
  detail: string
  waitingForPosting?: boolean
}

export function summarizeFiveChecks(items: ClosingReadinessItem[]): FiveCheckSummaryItem[] {
  return FIVE_CLOSING_CHECKS.map(({ code, label }) => {
    const item = items.find(row => row.code === code)
    return {
      code,
      label,
      status: item?.status ?? 'incomplete',
      detail: item?.detail ?? '本期未返回该项核验结果。',
      ...(item?.waitingForPosting ? { waitingForPosting: true } : {}),
    }
  })
}

/** 五项之外、仍未完成的准备项数量（折旧、摊销、工资、关账顺序等；不含仅等过账的连带项）。 */
export function countOtherPendingReadiness(items: ClosingReadinessItem[]): number {
  return items.filter(
    item => !FIVE_CLOSING_CHECKS.some(def => def.code === item.code)
      && !['complete', 'not_required'].includes(item.status)
      && !item.waitingForPosting,
  ).length
}

// ── P1: 根因聚合 —— 把五项检查结果折叠成"还差几步能结账"的任务队列 ──

export interface ClosingTaskAction {
  label: string
  kind: 'run-closing' | 'vouchers' | 'match' | 'detail' | 'walkthrough' | 'balance-detail' | 'import-bill'
  statementId?: string
  route?: string
  /** detail 动作：切到该月份继续处理（关账顺序） */
  month?: string
}

export interface ClosingTaskMatchDetail {
  statementId: string
  date: string
  description: string
  reason: string
}

export interface ClosingTask {
  id: string
  title: string
  count?: number
  detail?: string
  affects: string[]
  actions: ClosingTaskAction[]
  severity: 'blocked' | 'needs_review'
  matchDetails?: ClosingTaskMatchDetail[]
}

const BOOK_MATCH_IGNORED_TYPES = new Set([
  'unreconciled', 'book_only', 'possible_duplicate_income',
  'bank_balance_mismatch', 'statement_balance_mismatch', 'unposted_match',
])

// 疑似重复收入的提示按平台点名（微信/支付宝口径不同，不能混说）
const PLATFORM_INCOME_LABELS: Record<string, string> = { wechat: '微信', alipay: '支付宝' }

export function deriveClosingTasks(checks: PeriodCloseChecks | null | undefined): ClosingTask[] {
  const allChecks = checks?.checks
  if (!allChecks || !Array.isArray(allChecks) || allChecks.length === 0) return []
  const find = (code: string) => allChecks.find(c => c.code === code)
  const tasks: ClosingTask[] = []

  const order = find('PERIOD_ORDER')
  if (order && normalizeStatus(order) !== 'complete') {
    const count = Number(order.earlierOpenCount || 0)
    const earliest = String(order.earliestOpenMonth || '')
    tasks.push({
      id: 'period-order',
      title: count === 1 ? '有 1 个更早的月份还没关账' : `有 ${count} 个更早的月份还没关账`,
      detail: earliest ? `请先完成 ${earliest} 的结账，再回来结本月。` : undefined,
      affects: ['关账顺序'],
      actions: earliest ? [{ label: `去结 ${earliest}`, kind: 'detail', month: earliest }] : [],
      severity: 'blocked',
    })
  }

  const blockedNames = allChecks
    .filter(c => c.code !== 'VOUCHER_TO_SOURCE' && Number(c.blockedByPendingCount || 0) > 0 && normalizeStatus(c) !== 'complete')
    .map(c => COPY[c.code]?.title || c.name || c.code)

  const voucher = find('VOUCHER_TO_SOURCE')
  if (voucher) {
    const review = Number(voucher.pendingReviewCount || 0)
    const orphan = Number(voucher.orphanVoucherCount || 0)
    if (review > 0) {
      tasks.push({
        id: 'review',
        title: `有 ${review} 张凭证待复核`,
        count: review,
        affects: blockedNames,
        actions: [{ label: '去凭证页', kind: 'vouchers', route: '/vouchers' }],
        severity: 'needs_review',
      })
    }
    if (orphan > 0) {
      tasks.push({
        id: 'orphan',
        title: `有 ${orphan} 张凭证缺少有效来源`,
        count: orphan,
        affects: blockedNames,
        actions: [{ label: '去凭证页', kind: 'vouchers', route: '/vouchers' }],
        severity: 'blocked',
      })
    }
  }

  const bank = find('BOOK_TO_ACTUAL')
  if (bank) {
    const issues = ((bank.issues as any[]) || []).filter(i => i.type !== 'unposted_match')
    const unreconciled = issues.filter(i => i.type === 'unreconciled')
    const bookOnly = issues.filter(i => i.type === 'book_only')
    // 缺来源是配对的副产物：确认配对时后端会补建来源行，所以并进同一张卡，
    // 不让用户面对「10 笔流水没配对 + 10 处数据要修复」这种同一件事的两个计数。
    const missingSource = issues.filter(i => i.type === 'missing_source')
    if (unreconciled.length > 0 || bookOnly.length > 0) {
      const title = unreconciled.length > 0 && bookOnly.length > 0
        ? `有 ${unreconciled.length} 笔银行流水和 ${bookOnly.length} 张账目凭证还没互相配对`
        : unreconciled.length > 0
          ? `有 ${unreconciled.length} 笔银行流水还没核对入账`
          : `有 ${bookOnly.length} 张凭证的分录还没找到银行流水`
      tasks.push({
        id: 'match',
        title,
        detail: missingSource.length ? `配对后会自动补建 ${missingSource.length} 条流水的来源记录。` : undefined,
        affects: ['银行流水核对'],
        actions: [{ label: '去对账', kind: 'match' }],
        severity: 'blocked',
        matchDetails: unreconciled.map(issue => ({
          statementId: String(issue.statementId || ''),
          date: String(issue.date || ''),
          description: String(issue.description || ''),
          reason: String(issue.reason || ''),
        })),
      })
    } else if (missingSource.length > 0) {
      // 没有可配对的凭证/流水时，缺来源才需要用户单独处理
      tasks.push({
        id: 'missing-source',
        title: `有 ${missingSource.length} 笔流水缺少来源记录`,
        detail: '重新导入或认领入账后会自动补建。',
        affects: ['银行流水核对'],
        actions: [{ label: '去处理', kind: 'detail' }],
        severity: 'blocked',
      })
    }
    const duplicates = issues.filter(i => i.type === 'possible_duplicate_income')
    if (duplicates.length > 0) {
      // 平台一致时点名（微信/支付宝），混合时统一说“平台”
      const labels = new Set(duplicates.map(i => PLATFORM_INCOME_LABELS[String(i.platform || '')] || '平台'))
      const platformLabel = labels.size === 1 ? [...labels][0] : '平台'
      tasks.push({
        id: 'duplicate',
        title: `有 ${duplicates.length} 笔到账疑似与${platformLabel}收入重复`,
        affects: ['银行流水核对'],
        actions: [{ label: '去核对', kind: 'match' }],
        severity: 'needs_review',
      })
    }
    // 账面余额只统计已过账凭证，故「账面 vs 银行期末」差异在还有凭证未过账时，
    // 大概率是未过账的下游结果——先隐藏，避免与「还没过账」重复展示成两个阻断；
    // 过账后若仍有差异会重新浮现。「流水不连续」与过账无关，始终展示。
    const voucherCheckForBalance = find('VOUCHER_TO_SOURCE')
    const voucherUnposted = Number((voucherCheckForBalance as any)?.autoPostAtCloseCount ?? (voucherCheckForBalance as any)?.draftCount ?? 0)
      + Number((voucherCheckForBalance as any)?.pendingReviewCount || 0)
    const projDiff = (bank.balanceReconciliation as any)?.projected_book_difference
    const projBalanced = projDiff === undefined || Math.abs(Number(projDiff)) <= 0.01
    const balance = issues
      .filter(i => i.type === 'bank_balance_mismatch' || i.type === 'statement_balance_mismatch')
      .filter(i => i.type === 'statement_balance_mismatch' || voucherUnposted <= 0 || !projBalanced)
    if (balance.length > 0) {
      const difference = (balance[0] as any).difference
      tasks.push({
        id: 'balance',
        title: '账面银行存款与银行期末余额对不上',
        detail: difference ? `差额 ${difference}` : undefined,
        affects: ['银行流水核对'],
        actions: [{ label: '查看核对明细', kind: 'balance-detail' }],
        severity: 'blocked',
      })
    }
    // missing_source 已并入配对卡或单独的「缺来源」卡，不再重复计一次
    const repair = issues.filter(i => !BOOK_MATCH_IGNORED_TYPES.has(String(i.type)) && i.type !== 'missing_source')
    if (repair.length > 0) {
      tasks.push({
        id: 'repair',
        title: `有 ${repair.length} 处对账数据需要修复`,
        affects: ['银行流水核对'],
        actions: [{ label: '查看核对明细', kind: 'detail' }],
        severity: 'blocked',
      })
    }
  }

  const intangible = find('INTANGIBLE_AMORTIZATION')
  if (intangible) {
    const pending = Number(intangible.pendingCount || 0)
    if (pending > 0 && normalizeStatus(intangible) !== 'complete') {
      tasks.push({
        id: 'intangible-amortization',
        title: pending === 1 ? '有 1 项无形资产还没摊销' : `有 ${pending} 项无形资产还没摊销`,
        count: pending,
        affects: ['无形资产摊销'],
        actions: [{ label: '去摊销', kind: 'detail', route: '/assets?tab=intangible' }],
        severity: 'blocked',
      })
    }
  }

  const clearing = find('PLATFORM_CLEARING_BALANCE')
  if (clearing && normalizeStatus(clearing) === 'blocked') {
    const accounts = (clearing.accounts as Array<{ accountName?: string; balance?: string }>) || []
    const first = accounts[0]
    const title = accounts.length === 1 && first?.accountName && first?.balance
      ? `${first.accountName}余额为负（¥${first.balance}）`
      : accounts.length > 1
        ? `有 ${accounts.length} 个平台待结算科目余额为负`
        : String(clearing.name || '平台待结算科目余额为负')
    tasks.push({
      id: 'platform-clearing-negative',
      title,
      detail: String(clearing.message || '可能有平台提现已入银行，但当月微信/支付宝交易账单尚未导入。'),
      affects: ['平台待结算核对'],
      actions: [{ label: '去导入平台账单', kind: 'import-bill' }],
      severity: 'blocked',
    })
  }

  return tasks
}

export interface ClosedMetricsSummary {
  monthlyIncome: number
  monthlyExpense: number
  netProfit: number
  interestIncome: number
}

interface IncomeStatementSummaryLike {
  rows?: Array<{ key?: string; values?: { current?: number | string | null } }>
  totals?: { netProfit?: { current?: number | string | null } }
}

const roundMoney2 = (value: number): number => {
  const rounded = Math.round((value + Number.EPSILON) * 100) / 100
  return Object.is(rounded, -0) ? 0 : rounded
}

/**
 * 汇总结账完成页「本月收入 / 成本费用 / 本月净利润」。
 * 银行存款利息收入在《小企业会计准则》下以负数冲减「财务费用」，此处将
 * finance_expenses < 0 归入收入侧（同时负 investment_income 归入成本费用侧），
 * 保持 收入 - 成本费用 = 净利润，消除「经营成本 -¥12.70」的反直觉展示。
 */
export function summarizeClosedIncomeStatement(
  statement: IncomeStatementSummaryLike | null | undefined,
): ClosedMetricsSummary {
  const rows = Array.isArray(statement?.rows) ? statement.rows : []
  const value = (key: string) => Number(rows.find(row => row.key === key)?.values?.current || 0)
  const operatingRevenue = value('operating_revenue')
  const nonoperatingIncome = value('nonoperating_income')
  const investmentIncome = value('investment_income')
  const financeExpenses = value('finance_expenses')
  const netProfit = roundMoney2(Number(statement?.totals?.netProfit?.current || 0))
  const interestIncome = financeExpenses < 0 ? roundMoney2(-financeExpenses) : 0
  const monthlyIncome = roundMoney2(
    operatingRevenue + nonoperatingIncome + Math.max(investmentIncome, 0) + interestIncome,
  )
  const monthlyExpense = roundMoney2(monthlyIncome - netProfit)
  return {
    monthlyIncome,
    monthlyExpense,
    netProfit,
    interestIncome,
  }
}

/** 仅支持导出补记台账的提示项白名单，避免预览/暂估等类型误触发补记导出接口 */
export const EXPORTABLE_NOTICE_CODES: Readonly<Record<string, string>> = {
  CROSS_PERIOD_PENDING: 'cross-period',
  BACKFILL_IN_PERIOD: 'backfill',
  VAT_PERIOD_MISMATCH: 'vat-period',
}

/** 返回提示项对应的导出 scope；非可导出类型返回 null */
export function noticeExportScope(notice: Pick<ClosingNotice, 'code'>): string | null {
  return EXPORTABLE_NOTICE_CODES[notice.code] ?? null
}

/** 映射结账前提示卡片的 NAlert 色调：小微免税利好预告统一用 success 浅绿 */
export function noticeAlertType(notice: Pick<ClosingNotice, 'code' | 'severity'>): 'success' | 'info' | 'warning' {
  if (notice.code === 'VAT_EXEMPTION_PREVIEW') return 'success'
  if (notice.severity === 'success' || notice.severity === 'info') return notice.severity
  return 'warning'
}

/** 格式化结账前提示标题，剔除启用前/免税预告中无信息量的笔数拼接 */
export function formatClosingNoticeTitle(notice: Pick<ClosingNotice, 'code' | 'name' | 'count' | 'amount'>): string {
  // 启用前的数据是「流水 N 条 + 工资表 N 份」，混算成「N 笔」会串味；具体构成由 message 说明，标题只报事项名。
  if (notice.code === 'PRE_BOOK_START_ACTIVITY') return notice.name
  if (notice.code === 'VAT_EXEMPTION_PREVIEW') {
    return notice.amount ? `小微免税预告 · 预计减免增值税 ¥${notice.amount}` : '小微免税预告'
  }
  const amount = notice.amount ? ` · ¥${notice.amount}` : ''
  return `${notice.name}（${notice.count} 笔${amount}）`
}

/** 判断提示项卡片是否需要渲染底部动作栏，无动作时不留空白行 */
export function hasClosingNoticeActions(notice: Pick<ClosingNotice, 'code'>): boolean {
  return Boolean(noticeExportScope(notice))
    || notice.code === 'PRE_BOOK_START_ACTIVITY'
    || notice.code === 'CROSS_PERIOD_PENDING'
    || notice.code === 'SALARY_PAYABLE_OUTSTANDING'
}

