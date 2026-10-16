/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  calculateDeadlineSummary,
  calculateExemptionGauge,
  formatCopyAmount,
  generateTaxCopilotInsight,
  isFilingCardPending,
  periodToQuarter,
  quarterLabel,
  resolveNextPendingCard,
  summarizeOtherTaxes,
} from '@/utils/taxFiling'

describe('tax filing calculation & intelligence copilot utilities', () => {
  describe('calculateExemptionGauge', () => {
    it('handles quarterly zero sales correctly', () => {
      const gauge = calculateExemptionGauge(0, '2026-Q3')
      expect(gauge.threshold).toBe(300000)
      expect(gauge.totalSales).toBe(0)
      expect(gauge.percent).toBe(0)
      expect(gauge.remaining).toBe(300000)
      expect(gauge.isExempt).toBe(true)
      expect(gauge.status).toBe('safe')
      expect(gauge.tip).toContain('全额免征增值税')
    })

    it('handles normal sales below quarterly 300k limit', () => {
      const gauge = calculateExemptionGauge(150000, '2026-Q3')
      expect(gauge.threshold).toBe(300000)
      expect(gauge.percent).toBe(50)
      expect(gauge.remaining).toBe(150000)
      expect(gauge.isExempt).toBe(true)
      expect(gauge.status).toBe('safe')
    })

    it('switches to warning status when close to threshold (>85%)', () => {
      const gauge = calculateExemptionGauge(270000, '2026-Q3')
      expect(gauge.percent).toBe(90)
      expect(gauge.isExempt).toBe(true)
      expect(gauge.status).toBe('warning')
    })

    it('marks danger and shows exceeded amount when sales exceed 300k', () => {
      const gauge = calculateExemptionGauge(350000, '2026-Q3')
      expect(gauge.isExempt).toBe(false)
      expect(gauge.status).toBe('danger')
      expect(gauge.remaining).toBe(0)
      expect(gauge.tip).toContain('已超免税限额 ¥50,000')
    })

    it('adapts to monthly threshold of 100k when periodType is month', () => {
      const gauge = calculateExemptionGauge(60000, '2026-09', 'month')
      expect(gauge.threshold).toBe(100000)
      expect(gauge.percent).toBe(60)
      expect(gauge.remaining).toBe(40000)
    })
  })

  describe('generateTaxCopilotInsight', () => {
    it('generates zero filing advice when sales and taxes are 0', () => {
      const insight = generateTaxCopilotInsight({
        vat: { totalSales: 0, isBelowThreshold: true, totalVatPayable: 0 },
        cit: { totalProfit: 0, preferentialTax: 0 },
        crossCheck: { consistent: true },
        summary: { vatPayable: 0, citPayable: 0 },
      })
      expect(insight.badgeText).toBe('建议零申报')
      expect(insight.badgeType).toBe('success')
      expect(insight.headline).toContain('一键零申报')
    })

    it('generates exemption advice when sales are under 300k', () => {
      const insight = generateTaxCopilotInsight({
        vat: { totalSales: 88000, isBelowThreshold: true, totalVatPayable: 0 },
        cit: { totalProfit: 12000, preferentialTax: 600 },
        crossCheck: { consistent: true },
        summary: { vatPayable: 0, citPayable: 600 },
      })
      expect(insight.badgeText).toBe('享受免税')
      expect(insight.badgeType).toBe('success')
      expect(insight.pathAdvice).toContain('免税销售额')
    })

    it('warns about cross-check diffs when sales are under threshold but diffs exist', () => {
      const insight = generateTaxCopilotInsight({
        vat: { totalSales: 88000, isBelowThreshold: true, totalVatPayable: 0 },
        cit: { totalProfit: 12000, preferentialTax: 600 },
        crossCheck: { consistent: false, differences: [{ label: '发票开具 vs 账载销售额' }] },
        summary: { vatPayable: 0, citPayable: 600 },
      })
      expect(insight.detail).toContain('存在差额')
    })

    it('warns about taxable obligation when sales exceed threshold', () => {
      const insight = generateTaxCopilotInsight({
        vat: { totalSales: 400000, isBelowThreshold: false, totalVatPayable: 3960.4 },
        cit: { totalProfit: 50000, preferentialTax: 2500 },
        crossCheck: { consistent: true },
        summary: { vatPayable: 3960.4, citPayable: 2500 },
      })
      expect(insight.badgeText).toBe('正常缴税申报')
      expect(insight.badgeType).toBe('warning')
      expect(insight.headline).toContain('3,960.40')
    })

    it('highlights blocking issues when blockers exist', () => {
      const insight = generateTaxCopilotInsight({
        summary: { blockingIssues: ['存在未记账凭证'] },
      })
      expect(insight.badgeText).toBe('申报受阻')
      expect(insight.badgeType).toBe('error')
    })
  })

  describe('formatCopyAmount', () => {
    it('formats numbers into two-decimal string suitable for copy-pasting', () => {
      expect(formatCopyAmount(1234.5)).toBe('1234.50')
      expect(formatCopyAmount('88.888')).toBe('88.89')
      expect(formatCopyAmount(null)).toBe('0.00')
    })
  })

  describe('calculateDeadlineSummary', () => {
    it('accurately calculates pending tasks and finds the nearest daysLeft', () => {
      const vatDeadline = { deadlineId: 'd1', status: 'pending', daysLeft: 11 }
      const citDeadline = { deadlineId: 'd2', status: 'filed', daysLeft: 11 }
      const finStatement = { deadlineId: 'd3', status: 'pending', daysLeft: 11 }
      const stampDuty = { deadlineId: 'd4', status: 'filed', daysLeft: 11 }

      const summary = calculateDeadlineSummary([vatDeadline, citDeadline, finStatement, stampDuty])
      expect(summary.pendingCount).toBe(2)
      expect(summary.overdueCount).toBe(0)
      expect(summary.isAllFiled).toBe(false)
      expect(summary.nearestDaysLeft).toBe(11)
    })

    it('prioritizes upcoming unexpired deadline when some historical items are overdue', () => {
      const vatDeadline = { deadlineId: 'd1', status: 'pending', daysLeft: 11 }
      const citDeadline = { deadlineId: 'd2', status: 'pending', daysLeft: 11 }
      const iitWithholding = { deadlineId: 'd3', status: 'pending', daysLeft: -26 }

      const summary = calculateDeadlineSummary([vatDeadline, citDeadline, iitWithholding])
      expect(summary.pendingCount).toBe(3)
      expect(summary.overdueCount).toBe(1)
      expect(summary.nearestDaysLeft).toBe(11)
    })

    it('returns closest overdue days when all pending items are overdue', () => {
      const item1 = { deadlineId: 'd1', status: 'pending', daysLeft: -5 }
      const item2 = { deadlineId: 'd2', status: 'pending', daysLeft: -12 }

      const summary = calculateDeadlineSummary([item1, item2])
      expect(summary.pendingCount).toBe(2)
      expect(summary.overdueCount).toBe(2)
      expect(summary.nearestDaysLeft).toBe(-5)
    })

    it('returns isAllFiled=true and nearestDaysLeft=null when all tasks are completed', () => {
      const item1 = { deadlineId: 'd1', status: 'filed', daysLeft: 0 }
      const item2 = { deadlineId: 'd2', status: 'filed', daysLeft: 5 }

      const summary = calculateDeadlineSummary([item1, item2])
      expect(summary.pendingCount).toBe(0)
      expect(summary.overdueCount).toBe(0)
      expect(summary.isAllFiled).toBe(true)
      expect(summary.nearestDaysLeft).toBeNull()
    })
  })

  describe('periodToQuarter', () => {
    it('maps monthly period (e.g. IIT withholding) to its containing quarter', () => {
      expect(periodToQuarter('2026-01')).toBe('2026-Q1')
      expect(periodToQuarter('2026-03')).toBe('2026-Q1')
      expect(periodToQuarter('2026-04')).toBe('2026-Q2')
      expect(periodToQuarter('2026-09')).toBe('2026-Q3')
      expect(periodToQuarter('2026-12')).toBe('2026-Q4')
    })

    it('passes quarter and other formats through unchanged', () => {
      expect(periodToQuarter('2026-Q1')).toBe('2026-Q1')
      expect(periodToQuarter('2026')).toBe('2026')
      expect(periodToQuarter('')).toBe('')
      expect(periodToQuarter(' 2026-01 ')).toBe('2026-Q1')
    })

    it('keeps malformed month values untouched', () => {
      expect(periodToQuarter('2026-13')).toBe('2026-13')
      expect(periodToQuarter('2026-00')).toBe('2026-00')
    })
  })

  describe('quarterLabel', () => {
    it('formats quarter period as Chinese label', () => {
      expect(quarterLabel('2026-Q1')).toBe('2026 年第 1 季度')
      expect(quarterLabel('2024-Q4')).toBe('2024 年第 4 季度')
    })

    it('returns non-quarter period as-is', () => {
      expect(quarterLabel('2026-01')).toBe('2026-01')
      expect(quarterLabel('')).toBe('')
    })
  })

  describe('summarizeOtherTaxes & switchable cards flow', () => {
    it('summarizes other taxes excluding fin_statements', () => {
      const checklist = [
        { taxKind: 'fin_statements', status: 'pending' },
        { taxKind: 'stamp_duty', status: 'pending' },
        { taxKind: 'iit_withholding', status: 'filed' },
      ]
      const summary = summarizeOtherTaxes(checklist)
      expect(summary.status).toBe('pending')
      expect(summary.totalCount).toBe(2)
      expect(summary.pendingCount).toBe(1)
      expect(summary.resolvedCount).toBe(1)
    })

    it('marks other taxes as filed when all non-fin items are filed or waived', () => {
      const checklist = [
        { taxKind: 'fin_statements', status: 'pending' },
        { taxKind: 'stamp_duty', status: 'filed' },
        { taxKind: 'iit_withholding', status: 'waived' },
      ]
      const summary = summarizeOtherTaxes(checklist)
      expect(summary.status).toBe('filed')
      expect(summary.pendingCount).toBe(0)
      expect(summary.resolvedCount).toBe(2)
    })

    it('resolves the next pending card sequentially and wraps around when needed', () => {
      const pkg = {
        vat: { deadline: { status: 'filed' } },
        cit: { deadline: { status: 'pending' } },
        checklist: [
          { taxKind: 'fin_statements', status: 'pending' },
          { taxKind: 'stamp_duty', status: 'pending' },
        ],
      }

      // 从总览出发应跳往首个待申报卡片 (cit)
      expect(resolveNextPendingCard(pkg, 'overview')).toBe('cit')
      // 刚办结 vat 后自动切到下一张待申报卡片 (cit)
      expect(resolveNextPendingCard(pkg, 'vat')).toBe('cit')

      // 假设用户在 fin_statements 办结后，下一张是 other_taxes
      const pkgAfterFin = {
        vat: { deadline: { status: 'pending' } },
        cit: { deadline: { status: 'filed' } },
        checklist: [
          { taxKind: 'fin_statements', status: 'filed' },
          { taxKind: 'stamp_duty', status: 'filed' },
        ],
      }
      // other_taxes 已办结，环绕回前面还未办结的 vat
      expect(resolveNextPendingCard(pkgAfterFin, 'fin_statements')).toBe('vat')
    })

    it('stays on other_taxes if there are still remaining pending items in other_taxes', () => {
      const pkg = {
        vat: { deadline: { status: 'filed' } },
        cit: { deadline: { status: 'filed' } },
        checklist: [
          { taxKind: 'fin_statements', status: 'filed' },
          { taxKind: 'stamp_duty', status: 'filed' },
          { taxKind: 'iit_withholding', status: 'pending' },
        ],
      }
      expect(isFilingCardPending(pkg, 'other_taxes')).toBe(true)
      expect(resolveNextPendingCard(pkg, 'other_taxes')).toBe('other_taxes')
    })

    it('returns null when all 4 filing cards are resolved', () => {
      const pkg = {
        vat: { deadline: { status: 'filed' } },
        cit: { deadline: { status: 'waived' } },
        checklist: [
          { taxKind: 'fin_statements', status: 'filed' },
          { taxKind: 'stamp_duty', status: 'filed' },
          { taxKind: 'iit_withholding', status: 'waived' },
        ],
      }
      expect(resolveNextPendingCard(pkg, 'other_taxes')).toBeNull()
    })
  })
})
