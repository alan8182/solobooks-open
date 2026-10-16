/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export interface TaxReportCheck {
  level: 'ok' | 'warning' | 'error'
  message: string
}

export interface TaxReportPreview {
  period: string
  companyName: string
  taxId: string
  checks: TaxReportCheck[]
  totals: Record<string, number>
}

export function previewSmallBusinessMonthQuarterTaxReport(
  period: string,
  periodType: 'month' | 'quarter' = 'month',
): Promise<TaxReportPreview> {
  return apiClient.get('/api/tax-report/xiaoqiye/month-quarter/preview', {
    params: { period, period_type: periodType },
  })
}

export function exportSmallBusinessMonthQuarterTaxReport(
  period: string,
  periodType: 'month' | 'quarter' = 'month',
): Promise<Blob> {
  return apiClient.get('/api/tax-report/xiaoqiye/month-quarter/export', {
    params: { period, period_type: periodType },
    responseType: 'blob',
  })
}
