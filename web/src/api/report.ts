/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import type {
  MonthlyReportResponse,
  ReportDiagnosisResponse,
  HealthScoreResponse,
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

export function getHealthScore(month: string, includeDiagnosis = false): Promise<HealthScoreResponse> {
  return apiClient.post('/api/health-score', { month, includeDiagnosis })
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

/** 会计视图：会企04表节次结构 × 小企业四组件，每行为「项目 × 各权益组件金额」 */
export interface EquityStandardColumn {
  key: string
  label: string
}

export interface EquityStandardRow {
  key: string
  label: string
  level?: number
  kind?: 'item' | 'section' | 'total'
  values: Record<string, number>
}

export interface EquityChangesResponse {
  year: number
  rows: EquityChangeRow[]
  netProfit: number
  totals: { priorYearEnd: number; increase: number; decrease: number; yearEnd: number }
  simplified: boolean
  standard?: { columns: EquityStandardColumn[]; rows: EquityStandardRow[] }
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
