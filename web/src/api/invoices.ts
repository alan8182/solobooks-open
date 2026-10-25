/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export interface InvoiceLedgerItem {
  id: string
  entryId: string | null
  direction: 'income' | 'expense'
  invoiceNumber: string
  invoiceCode: string
  invoiceType: string
  invoiceStatus: string
  invoiceDate: string
  buyerName: string
  buyerTaxId: string
  sellerName: string
  sellerTaxId: string
  amountExclTax: number
  taxAmount: number
  amountInclTax: number
  taxRate: string
  itemName: string
  remark: string
  partnerId: string | null
  source: string
  voucherRef: string
}

export interface InvoiceListResponse {
  items: InvoiceLedgerItem[]
  total: number
  page: number
  pageSize: number
}

export interface InvoiceSummaryBucket {
  month: string
  incomeCount: number
  incomeAmount: number
  incomeTax: number
  expenseCount: number
  expenseAmount: number
  expenseTax: number
}

export interface InvoiceSummaryResponse {
  months: InvoiceSummaryBucket[]
  totals: {
    incomeCount: number
    incomeAmount: number
    incomeTax: number
    expenseCount: number
    expenseAmount: number
    expenseTax: number
    netTax: number
  }
}

export function getInvoices(params: {
  month_from?: string
  month_to?: string
  direction?: string
  keyword?: string
  status?: string
  page?: number
  page_size?: number
}): Promise<InvoiceListResponse> {
  return apiClient.get('/api/invoices', { params })
}

export function getInvoiceSummary(params: { month_from?: string; month_to?: string }): Promise<InvoiceSummaryResponse> {
  return apiClient.get('/api/invoices/summary', { params })
}

export function backfillInvoices(): Promise<{ scanned: number; created: number }> {
  return apiClient.post('/api/invoices/backfill')
}
