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
}

const COPY: Record<string, Omit<ClosingReadinessItem, 'status'>> = {
  VOUCHER_TO_SOURCE: {
    code: 'VOUCHER_TO_SOURCE', title: '账证核对', detail: '确认每笔凭证都能追溯到原始业务记录。', actionLabel: '查看待处理记录', step: 1,
  },
  LEDGER_TO_LEDGER: {
    code: 'LEDGER_TO_LEDGER', title: '账账核对', detail: '确认借方和贷方金额一致。', actionLabel: '查看核对明细', step: 4,
  },
  BOOK_TO_ACTUAL: {
    code: 'BOOK_TO_ACTUAL', title: '银行流水核对', detail: '确认账面记录与实际资金流水一致。', actionLabel: '去核对', step: 4,
  },
  LEDGER_TO_REPORT: {
    code: 'LEDGER_TO_REPORT', title: '账表核对', detail: '确认账册余额可以正确汇总到报表。', actionLabel: '查看核对明细', step: 4,
  },
  REPORT_TO_TAX: {
    code: 'REPORT_TO_TAX', title: '税表勾稽', detail: '确认本期记账规则和税表数据完整。', actionLabel: '查看待补充项', step: 1,
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
    const pending = Number(check.pendingCount || 0)
    const orphan = Number(check.orphanVoucherCount || 0)
    if (pending || orphan) return `${pending + orphan} 项记录还不能完整追溯。`
  }
  if (check.code === 'BOOK_TO_ACTUAL') {
    const pending = Number(check.unreconciledBankCount || 0)
    if (pending) return `${pending} 笔银行流水还未核对。`
  }
  if (check.code === 'REPORT_TO_TAX') {
    const pending = Number(check.missingRuleVersionCount || 0)
    if (pending) return `${pending} 笔记录缺少本期记账规则。`
  }
  return fallback
}

export function getClosingReadiness(checks: PeriodCloseChecks | null | undefined): ClosingReadinessItem[] {
  return (checks?.checks || []).map((check) => {
    const copy = COPY[check.code] || {
      code: check.code,
      title: check.name || check.code,
      detail: '查看这项结账检查的结果。',
      actionLabel: '查看详情',
      step: 4,
    }
    return {
      ...copy,
      status: normalizeStatus(check),
      detail: detailFor(check, copy.detail),
    }
  })
}

export function firstActionableReadinessItem(items: ClosingReadinessItem[]): ClosingReadinessItem | undefined {
  return items.find((item) => ['blocked', 'needs_review', 'incomplete'].includes(item.status))
}
