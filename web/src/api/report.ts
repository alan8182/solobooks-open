/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import type {
  MonthlyReportResponse,
  ReportDiagnosisResponse,
  HealthScoreResponse,
  VatAssistResponse,
  CitAssistResponse,
  ArchiveChecklistResponse,
  BalanceSheetResponse,
  CashFlowResponse,
  IncomeStatementResponse,
  IncomeStatementViewMode,
  IncomeStatementBasis,
  ComparisonResponse,
} from './types'

export function getMonthlyReport(
  period: string,
  periodType: 'month' | 'quarter' | 'year' = 'month',
  includeSummary = false,
  includeDrafts = false,
): Promise<MonthlyReportResponse> {
  const payload: Record<string, unknown> = {
    month: period,
    period,
    periodType,
  }
  if (includeSummary) {
    payload.includeSummary = true
  }
  if (includeDrafts) {
    payload.includeDrafts = true
  }
  return apiClient.post('/api/report/monthly', payload)
}

export function getReportDiagnosis(
  period: string,
  periodType: 'month' | 'quarter' | 'year' = 'month',
  includeDrafts = false,
): Promise<ReportDiagnosisResponse> {
  return apiClient.post('/api/report/diagnosis', { month: period, period, periodType, includeDrafts })
}

export function getHealthScore(month: string): Promise<HealthScoreResponse> {
  return apiClient.post('/api/health-score', { month })
}

export function getVatAssist(
  period: string,
  periodType: 'month' | 'quarter' = 'quarter',
  taxpayerType = 'small_scale',
): Promise<VatAssistResponse> {
  return apiClient.post('/api/report/vat-assist', { period, periodType, taxpayerType })
}

export function getCitAssist(period: string, inputs: Record<string, unknown> = {}): Promise<CitAssistResponse> {
  return apiClient.post('/api/report/cit-assist', { period, periodType: 'quarter', ...inputs })
}

export function getArchiveChecklist(month: string): Promise<ArchiveChecklistResponse> {
  return apiClient.post('/api/report/archive-checklist', { month })
}

export function getBalanceSheet(month: string, periodType: 'month' | 'quarter' = 'month'): Promise<BalanceSheetResponse> {
  return apiClient.post('/api/report/balance-sheet', { month, periodType })
}

export function getCashFlow(month: string, periodType: 'month' | 'quarter' = 'month'): Promise<CashFlowResponse> {
  return apiClient.post('/api/report/cash-flow', { month, periodType })
}

export interface EquityChangeRow {
  key: string
  label: string
  priorYearEnd: number
  increase: number
  decrease: number
  yearEnd: number
  isRetained?: boolean
}

export interface EquityChangesResponse {
  year: number
  rows: EquityChangeRow[]
  netProfit: number
  totals: { priorYearEnd: number; increase: number; decrease: number; yearEnd: number }
  simplified: boolean
}

export function getEquityChanges(year: number): Promise<EquityChangesResponse> {
  return apiClient.post('/api/report/equity-changes', { year })
}

export function getIncomeStatement(
  month: string,
  viewMode: IncomeStatementViewMode = 'summary',
  basis: IncomeStatementBasis = 'posting',
): Promise<IncomeStatementResponse> {
  return apiClient.post('/api/report/income-statement', { month, viewMode, basis })
}

export function getComparison(
  base_month: string,
  compare_mode: 'mom' | 'yoy',
): Promise<ComparisonResponse> {
  return apiClient.post('/api/report/comparison', { base_month, compare_mode })
}

export function getAIDiagnosis(month: string): Promise<any> {
  return apiClient.post('/api/report/ai-diagnosis', { month })
}
