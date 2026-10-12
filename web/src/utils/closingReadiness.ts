export type CloseCheckStatus = 'complete' | 'blocked' | 'needs_review' | 'not_required' | 'incomplete'

export interface PeriodCloseCheck {
  code: string
  name: string
  status: string
  uiStatus?: CloseCheckStatus
  [key: string]: unknown
}

export interface PeriodCloseChecks {
  period?: string
  periodStatus?: string
  canClose?: boolean
  blockingCount?: number
  checks?: PeriodCloseCheck[]
}

export interface ClosingReadinessItem {
  code: string
  title: string
  status: CloseCheckStatus
  detail: string
  actionLabel: string
  step: number
  route?: string
}

const COPY: Record<string, Omit<ClosingReadinessItem, 'status'>> = {
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
      return issues.length === 0 || issues.every(issue => issue.type === 'unposted_match' && draftIds.has(String(issue.entryId)))
    }
    return false
  })
}

export function getClosingReadiness(checks: PeriodCloseChecks | null | undefined): ClosingReadinessItem[] {
  const batchPostable = canBatchPostDrafts(checks)
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

    if (batchPostable) {
      if (check.code === 'VOUCHER_TO_SOURCE') {
        const count = Number(check.autoPostAtCloseCount ?? check.draftCount ?? 0)
        status = 'complete'
        detail = `本月有 ${count} 笔凭证将在确认结账时自动批量过账。`
      } else if (status === 'needs_review' || (status === 'blocked' && check.code === 'BOOK_TO_ACTUAL' && Number(check.unreconciledBankCount || 0) === 0)) {
        status = 'complete'
        detail = '已就绪（将在结账自动过账后一并核验归档）。'
      }
    }

    return {
      ...copy,
      status,
      detail,
    }
  })
}

export function firstActionableReadinessItem(items: ClosingReadinessItem[]): ClosingReadinessItem | undefined {
  return items.find((item) => ['blocked', 'needs_review', 'incomplete'].includes(item.status))
}

// ── P1: 根因聚合 —— 把五项检查结果折叠成"还差几步能结账"的任务队列 ──

export interface ClosingTaskAction {
  label: string
  kind: 'run-closing' | 'vouchers' | 'match' | 'detail'
  statementId?: string
  route?: string
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

export function deriveClosingTasks(checks: PeriodCloseChecks | null | undefined): ClosingTask[] {
  const allChecks = checks?.checks
  if (!allChecks || !Array.isArray(allChecks) || allChecks.length === 0) return []
  const find = (code: string) => allChecks.find(c => c.code === code)
  const tasks: ClosingTask[] = []

  const blockedNames = allChecks
    .filter(c => c.code !== 'VOUCHER_TO_SOURCE' && Number(c.blockedByPendingCount || 0) > 0 && normalizeStatus(c) !== 'complete')
    .map(c => COPY[c.code]?.title || c.name || c.code)

  const voucher = find('VOUCHER_TO_SOURCE')
  if (voucher) {
    const draft = Number(voucher.autoPostAtCloseCount ?? voucher.draftCount ?? 0)
    const review = Number(voucher.pendingReviewCount || 0)
    const orphan = Number(voucher.orphanVoucherCount || 0)
    if (draft > 0) {
      const actions: ClosingTaskAction[] = []
      if (canBatchPostDrafts(checks)) actions.push({ label: `自动过账 ${draft} 张并结账`, kind: 'run-closing' })
      actions.push({ label: '去凭证页', kind: 'vouchers', route: '/vouchers' })
      tasks.push({
        id: 'post',
        title: draft === 1 ? '有 1 张凭证还没过账' : `有 ${draft} 张凭证还没过账`,
        count: draft,
        affects: blockedNames,
        actions,
        severity: 'blocked',
      })
    }
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
    if (unreconciled.length > 0 || bookOnly.length > 0) {
      tasks.push({
        id: 'match',
        title: unreconciled.length > 0 && bookOnly.length > 0
          ? `有 ${unreconciled.length} 笔银行流水和 ${bookOnly.length} 张账目凭证还没互相配对`
          : `有 ${Math.max(unreconciled.length, bookOnly.length)} 笔银行流水还没和账目配对`,
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
    }
    const duplicates = issues.filter(i => i.type === 'possible_duplicate_income')
    if (duplicates.length > 0) {
      tasks.push({
        id: 'duplicate',
        title: `有 ${duplicates.length} 笔到账疑似与微信收入重复`,
        affects: ['银行流水核对'],
        actions: [{ label: '去核对', kind: 'match' }],
        severity: 'needs_review',
      })
    }
    const balance = issues.filter(i => i.type === 'bank_balance_mismatch' || i.type === 'statement_balance_mismatch')
    if (balance.length > 0) {
      const difference = (balance[0] as any).difference
      tasks.push({
        id: 'balance',
        title: '账面银行存款与银行期末余额对不上',
        detail: difference ? `差额 ${difference}` : undefined,
        affects: ['银行流水核对'],
        actions: [{ label: '查看核对明细', kind: 'detail' }],
        severity: 'blocked',
      })
    }
    const repair = issues.filter(i => !BOOK_MATCH_IGNORED_TYPES.has(String(i.type)))
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

  return tasks
}
