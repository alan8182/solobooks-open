import apiClient from './client'
import type {
  MonthlyReportResponse,
  HealthScoreResponse,
  VatAssistResponse,
  CitAssistResponse,
  ArchiveChecklistResponse,
  TaxSummaryResponse,
  BalanceSheetResponse,
  CashFlowResponse,
  IncomeStatementResponse,
  IncomeStatementViewMode,
  ComparisonResponse,
} from './types'

export function getMonthlyReport(month: string): Promise<MonthlyReportResponse> {
  return apiClient.post('/api/report/monthly', { month })
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

export function getTaxSummary(
  period: string,
  periodType: 'month' | 'quarter' = 'quarter',
): Promise<TaxSummaryResponse> {
  return apiClient.post('/api/report/tax-summary', { period, periodType })
}

export function getBalanceSheet(month: string): Promise<BalanceSheetResponse> {
  return apiClient.post('/api/report/balance-sheet', { month })
}

export function getCashFlow(month: string): Promise<CashFlowResponse> {
  return apiClient.post('/api/report/cash-flow', { month })
}

export function getIncomeStatement(
  month: string,
  viewMode: IncomeStatementViewMode = 'summary',
): Promise<IncomeStatementResponse> {
  return apiClient.post('/api/report/income-statement', { month, viewMode })
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
