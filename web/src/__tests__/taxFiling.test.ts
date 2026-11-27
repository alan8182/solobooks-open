/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  buildVatFilingGuide,
  calculateDeadlineSummary,
  calculateExemptionGauge,
  formatCopyAmount,
  generateTaxCopilotInsight,
  isFilingCardPending,
  periodToQuarter,
  quarterLabel,
  recommendVatFilingMode,
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

    it('switches income-tax wording for entities without corporate income tax', () => {
      const insight = generateTaxCopilotInsight({
        vat: { totalSales: 0, isBelowThreshold: true, totalVatPayable: 0 },
        cit: null,
        citNotice: { citApplicable: false, incomeTaxLabel: '经营所得个人所得税' },
        crossCheck: { consistent: true },
        summary: { vatPayable: 0, citPayable: 0 },
      })
      expect(insight.detail).toContain('经营所得个人所得税')
      expect(insight.detail).not.toContain('企业所得税')
    })

    it('does not promise CIT estimate for entities without corporate income tax', () => {
      const insight = generateTaxCopilotInsight({
        vat: { totalSales: 400000, isBelowThreshold: false, totalVatPayable: 3960.4 },
        cit: null,
        citNotice: { citApplicable: false, incomeTaxLabel: '经营所得个人所得税' },
        crossCheck: { consistent: true },
        summary: { vatPayable: 3960.4, citPayable: 0 },
      })
      expect(insight.detail).toContain('经营所得（5%–35%）')
      expect(insight.detail).not.toContain('企业所得税')
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

    it('surfaces the overdue item instead of letting upcoming deadlines mask it', () => {
      const vatDeadline = { deadlineId: 'd1', status: 'pending', daysLeft: 11 }
      const citDeadline = { deadlineId: 'd2', status: 'pending', daysLeft: 11 }
      const iitWithholding = { deadlineId: 'd3', status: 'pending', daysLeft: -26 }

      const summary = calculateDeadlineSummary([vatDeadline, citDeadline, iitWithholding])
      expect(summary.pendingCount).toBe(3)
      expect(summary.overdueCount).toBe(1)
      expect(summary.nearestDaysLeft).toBe(-26)
    })

    it('does not let a far-future annual fund deadline masquerade as the nearest countdown', () => {
      // 2026-10-25 真实账套复现：Q3 五税种逾期 3 天 + 残保金（次年 9-30，剩 340 天）
      const quarterItems = Array.from({ length: 5 }, (_, i) => ({
        deadlineId: `q${i}`,
        status: 'pending',
        daysLeft: -3,
      }))
      const disabilityFund = { deadlineId: 'df', status: 'pending', daysLeft: 340 }

      const summary = calculateDeadlineSummary([...quarterItems, disabilityFund])
      expect(summary.overdueCount).toBe(5)
      expect(summary.nearestDaysLeft).toBe(-3)
    })

    it('falls back to the nearest upcoming deadline when nothing is overdue', () => {
      const vat = { deadlineId: 'd1', status: 'pending', daysLeft: 82 }
      const citAnnual = { deadlineId: 'd2', status: 'pending', daysLeft: 218 }
      const disabilityFund = { deadlineId: 'd3', status: 'pending', daysLeft: 340 }

      const summary = calculateDeadlineSummary([vat, citAnnual, disabilityFund])
      expect(summary.overdueCount).toBe(0)
      expect(summary.nearestDaysLeft).toBe(82)
    })

    it('returns the most severe overdue days when all pending items are overdue', () => {
      const item1 = { deadlineId: 'd1', status: 'pending', daysLeft: -5 }
      const item2 = { deadlineId: 'd2', status: 'pending', daysLeft: -12 }

      const summary = calculateDeadlineSummary([item1, item2])
      expect(summary.pendingCount).toBe(2)
      expect(summary.overdueCount).toBe(2)
      expect(summary.nearestDaysLeft).toBe(-12)
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

  describe('buildVatFilingGuide', () => {
    const belowData = {
      periodType: 'quarter',
      totalSales: 88000,
      goodsLaborSales: 30000,
      serviceSales: 58000,
      isBelowThreshold: true,
      vatPayable: 0,
      invoiceTotal: 0,
    }

    it('keeps the confirm-mode entry when there is no uninvoiced income', () => {
      const guide = buildVatFilingGuide(belowData, 'confirm')

      expect(guide.steps[0].title).toContain('本期销售情况')
      expect(guide.steps.some(step => step.title.includes('业务信息表'))).toBe(false)
      expect(guide.warnings.join('')).toContain('自动归集发票')
    })

    it('recommends form mode when there is uninvoiced income', () => {
      expect(recommendVatFilingMode({ ...belowData, uninvoicedPositiveSales: 88000 })).toBe('form')
      expect(recommendVatFilingMode({ ...belowData, uninvoicedNegativeSales: -500 })).toBe('form')
      expect(recommendVatFilingMode(belowData)).toBe('confirm')
    })

    it('form mode routes uninvoiced income through 业务信息表 and 附1', () => {
      const guide = buildVatFilingGuide(
        { ...belowData, uninvoicedPositiveSales: 5000 },
        'form',
      )
      const text = guide.steps.map(step => `${step.title} ${step.detail}`).join('\n')

      expect(guide.steps[0].title).toContain('报表列表')
      expect(text).toContain('未开具发票销售业务')
      expect(text).toContain('附列资料（一）附1')
      // 回归：填表式不能把用户又指回「本期销售情况」入口
      expect(guide.steps.some(step => step.title.includes('本期销售情况'))).toBe(false)
      const unfilled = guide.steps.find(step => step.title.includes('附1'))
      expect(unfilled?.amounts?.[0]).toMatchObject({ amount: 5000 })
    })

    it('form mode over threshold includes 减免税明细表 and 主表应补税额', () => {
      const guide = buildVatFilingGuide({
        periodType: 'quarter',
        totalSales: 101000,
        goodsLaborSales: 101000,
        serviceSales: 0,
        isBelowThreshold: false,
        vatPayable: 1000,
        vatReduction: 2000,
        surchargeTotal: 60,
        uninvoicedPositiveSales: 101000,
        invoiceTotal: 0,
      }, 'form')

      const relief = guide.steps.find(step => step.title.includes('减免税'))
      expect(relief?.amounts?.[0].amount).toBe(2000)
      const main = guide.steps.find(step => step.title.startsWith('主表'))
      expect(main?.amounts?.some(row => row.label.includes('第 22 栏') && row.amount === 1000)).toBe(true)
      expect(guide.warnings.join('')).toContain('减税项目没填对')
    })

    it('form mode below threshold pins the exempt amount to 主表第 10 栏', () => {
      const guide = buildVatFilingGuide(
        { ...belowData, uninvoicedPositiveSales: 88000 },
        'form',
      )
      const main = guide.steps.find(step => step.title.includes('主表'))

      expect(main?.amounts?.[0].label).toContain('第 10 栏')
      expect(main?.amounts?.[0].amount).toBe(88000)
      expect(main?.detail).toContain('第 1 栏')
    })

    it('warns about negative uninvoiced adjustments and their 属期', () => {
      const guide = buildVatFilingGuide({
        ...belowData,
        totalSales: 300,
        uninvoicedPositiveSales: 500,
        uninvoicedNegativeSales: -200,
      }, 'form')
      const text = guide.steps.map(step => `${step.title} ${step.detail}`).join('\n')

      expect(text).toContain('第 4 列')
      expect(text).toContain('属期')
      expect(guide.warnings.join('')).toContain('不存在有效申报记录')
    })

    it('stops at a reconcile-first step when the period total is negative', () => {
      const guide = buildVatFilingGuide({ totalSales: -200 }, 'form')

      expect(guide.steps).toHaveLength(1)
      expect(guide.steps[0].title).toContain('先核对再申报')
      expect(guide.warnings.join('')).toContain('负数冲减')
    })

    it('does not short-circuit to zero filing when there are uninvoiced adjustments', () => {
      const guide = buildVatFilingGuide({
        ...belowData,
        totalSales: 0,
        uninvoicedPositiveSales: 200,
        uninvoicedNegativeSales: -200,
      }, 'form')

      expect(guide.steps.some(step => step.title.includes('无需填报销售额'))).toBe(false)
      expect(guide.steps.some(step => step.title.includes('附1'))).toBe(true)
    })

    it('keeps a two-step zero filing card in both modes', () => {
      expect(buildVatFilingGuide({ totalSales: 0 }, 'confirm').steps).toHaveLength(2)
      expect(buildVatFilingGuide({ totalSales: 0 }, 'form').steps).toHaveLength(2)
    })
  })
})
