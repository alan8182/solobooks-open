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
  /** 电子税务局查验状态（用户人工标记，财会〔2020〕6号第三条） */
  verifyStatus?: 'verified' | 'unverified'
  verifiedAt?: string
  /** 附件聚合（只算有效件） */
  attachmentCount?: number
  hasOriginalXml?: boolean
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
  posting?: string
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

/* ── 发票电子原件（attachments，Phase 1 单据整理） ── */

export interface InvoiceParsedSummary {
  invoiceNumber: string
  issueTime: string
  generalOrSpecial: string
  taxpayerType: string
  isBlue: boolean
  sellerName: string
  buyerName: string
  amountIncl: string
  taxAmount: string
  itemsCount: number
  hasTaxBureauSignature: boolean
  withholdingNotice: boolean
  warnings: string[]
}

/** 数电票 XML 派生的导入行（键与 invoice_service 导入管线一致） */
export interface InvoiceImportRow {
  invoice_number: string
  invoice_type?: string
  invoice_date: string
  direction: string
  total_amount: string
  tax_amount: string
  amountExcludingTax: string
  tax_rate: string
  item_name: string
  seller_name: string
  seller_tax_id: string
  buyer_name: string
  buyer_tax_id: string
  remark?: string
}

export interface InvoiceAttachmentItem {
  id: string
  invoiceId: string | null
  documentType: 'einvoice_xml' | 'einvoice_pdf' | 'einvoice_ofd' | 'other'
  fileName: string
  fileExt: string
  sizeBytes: number
  contentSha256: string
  status: 'active' | 'void'
  source: string
  voidedAt: string
  voidReason: string
  createdAt: string
  parsedSummary: InvoiceParsedSummary | null
  /** 仅 parked 列表返回：一键生成凭证草稿的输入行 */
  importRows?: InvoiceImportRow[] | null
}

export type InvoiceUploadOutcome = 'matched' | 'parked' | 'stored' | 'duplicate' | 'rejected'

export interface InvoiceUploadResultItem {
  fileName: string
  outcome: InvoiceUploadOutcome
  attachmentId: string | null
  invoiceId: string | null
  invoiceNumber: string
  warnings: string[]
  error: string
  matchedInvoice: {
    id: string
    invoiceNumber: string
    direction: string
    amountInclTax: string
  } | null
  importRows: InvoiceImportRow[] | null
}

export function uploadInvoiceAttachments(files: File[], invoiceId?: string): Promise<InvoiceUploadResultItem[]> {
  const form = new FormData()
  for (const file of files) {
    form.append('files', file)
  }
  if (invoiceId) {
    form.append('invoice_id', invoiceId)
  }
  return apiClient.post('/api/invoices/attachments', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

export function getInvoiceAttachments(params: { invoice_id?: string; parked?: boolean }): Promise<{
  items: InvoiceAttachmentItem[]
  total: number
}> {
  return apiClient.get('/api/invoices/attachments', { params })
}

export function getParkedAttachmentCount(): Promise<{ count: number }> {
  return apiClient.get('/api/invoices/attachments/parked-count')
}

/** blob 直通（响应拦截器不回传 headers），文件名由调用方从列表行取 */
export function downloadInvoiceAttachment(id: string): Promise<Blob> {
  return apiClient.get(`/api/invoices/attachments/${id}/download`, { responseType: 'blob', timeout: 120000 })
}

export function voidInvoiceAttachment(id: string, reason: string): Promise<InvoiceAttachmentItem> {
  return apiClient.post(`/api/invoices/attachments/${id}/void`, { reason })
}

export function relinkInvoiceAttachments(): Promise<{ relinked: number; scanned: number }> {
  return apiClient.post('/api/invoices/attachments/relink')
}

export function setInvoiceVerify(
  invoiceId: string,
  verified: boolean,
): Promise<{ id: string; verifyStatus: string; verifiedAt: string }> {
  return apiClient.patch(`/api/invoices/${invoiceId}/verify`, { verified })
}
