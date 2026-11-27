/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export type VoucherAttachmentType =
  | 'bank_receipt'
  | 'payment_screenshot'
  | 'reimbursement_form'
  | 'payroll_sheet'
  | 'contract'
  | 'other'

export const VOUCHER_ATTACHMENT_TYPE_OPTIONS: Array<{ label: string; value: VoucherAttachmentType }> = [
  { label: '银行回单', value: 'bank_receipt' },
  { label: '支付截图', value: 'payment_screenshot' },
  { label: '报销单', value: 'reimbursement_form' },
  { label: '工资表', value: 'payroll_sheet' },
  { label: '合同', value: 'contract' },
  { label: '其他', value: 'other' },
]

export const VOUCHER_ATTACHMENT_TYPE_LABELS: Record<string, string> = {
  einvoice_xml: 'XML 原件',
  einvoice_pdf: 'PDF 版式',
  einvoice_ofd: 'OFD 版式',
  bank_receipt: '银行回单',
  bank_receipt_batch: '回单合集',
  bank_statement_pdf: '银行对账单',
  payment_screenshot: '支付截图',
  reimbursement_form: '报销单',
  payroll_sheet: '工资表',
  contract: '合同',
  other: '其他',
}

export const VOUCHER_ATTACHMENT_ACCEPT = '.png,.jpg,.jpeg,.webp,.pdf,.ofd'
export const BANK_RECEIPT_ACCEPT = '.zip,.pdf,.png,.jpg,.jpeg,.webp'

export interface VoucherAttachmentItem {
  id: string
  companyId?: string
  invoiceId?: string | null
  bankStatementId?: string | null
  journalEntryId?: string | null
  period?: string | null
  parentAttachmentId?: string | null
  documentType: string
  fileName: string
  fileExt: string
  sizeBytes: number
  contentSha256: string
  status: string
  voidedAt?: string
  voidReason?: string | null
  parsedSummary?: Record<string, unknown> | null
  parsedMeta?: Record<string, unknown> | null
  parsedJson?: Record<string, unknown> | null
  relation?: 'direct' | 'invoice' | 'bank_statement' | 'bank_receipt' | string
  source?: string
  invoiceNumber?: string
  createdAt: string
}

export interface BankReceiptUploadResult {
  files: Array<{
    fileName: string
    outcome: string
    sliceCount?: number
    createdCount?: number
    periods?: string[]
    error?: string
  }>
  receipts: VoucherAttachmentItem[]
  statements: VoucherAttachmentItem[]
  summary: {
    batchCount: number
    receiptTotal: number
    matchedCount: number
    parkedCount: number
    duplicateCount: number
    statementPdfCount: number
    rejectedCount: number
  }
}

export function isImageAttachment(fileNameOrExt: string | undefined): boolean {
  const clean = String(fileNameOrExt || '').toLowerCase()
  const ext = clean.includes('.') ? clean.split('.').pop() || '' : clean
  return ['png', 'jpg', 'jpeg', 'webp'].includes(ext)
}

export function voucherAttachmentTypeLabel(documentType: string): string {
  return VOUCHER_ATTACHMENT_TYPE_LABELS[documentType] || '其他'
}

export function uploadBankReceipts(files: File[], useVision = true): Promise<BankReceiptUploadResult> {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  return apiClient.post('/api/bank/receipts/upload', form, {
    params: { use_vision: useVision },
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 180000,
  })
}

export function listBankReceipts(params?: {
  parked?: boolean
  month?: string
  statementId?: string
}): Promise<{ items: VoucherAttachmentItem[]; total: number }> {
  return apiClient.get('/api/bank/receipts', {
    params: {
      parked: params?.parked ?? false,
      month: params?.month || undefined,
      statement_id: params?.statementId || undefined,
    },
  })
}

export function relinkBankReceipts(): Promise<{ relinked: number; reextracted?: number; scanned: number }> {
  return apiClient.post('/api/bank/receipts/relink', undefined, { timeout: 180000 })
}

export function bindBankReceipt(attachmentId: string, statementId: string): Promise<VoucherAttachmentItem> {
  return apiClient.post(`/api/bank/receipts/${encodeURIComponent(attachmentId)}/bind`, { statementId })
}

export function listVoucherAttachments(
  entryId: string,
  includeVoid = true,
): Promise<{ items: VoucherAttachmentItem[]; activeCount?: number; total?: number }> {
  return apiClient.get(`/api/vouchers/${encodeURIComponent(entryId)}/attachments`, {
    params: { include_void: includeVoid },
  })
}

export interface VoucherAttachmentUploadItem {
  fileName: string
  outcome: 'stored' | 'duplicate' | 'rejected' | string
  attachmentId?: string | null
  journalEntryId?: string
  documentType?: string
  error?: string
  attachment?: VoucherAttachmentItem | null
}

export function uploadVoucherAttachments(
  entryId: string,
  documentType: VoucherAttachmentType,
  files: File[],
): Promise<{
  items: VoucherAttachmentUploadItem[]
  count: number
  storedCount?: number
  duplicateCount?: number
  rejectedCount?: number
}> {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  return apiClient.post(`/api/vouchers/${encodeURIComponent(entryId)}/attachments`, form, {
    params: { document_type: documentType },
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

export function fetchAttachmentPreviewBlob(attachmentId: string): Promise<Blob> {
  return apiClient.get(`/api/attachments/${encodeURIComponent(attachmentId)}/preview`, {
    responseType: 'blob',
  })
}

export function downloadAttachmentBlob(attachmentId: string): Promise<Blob> {
  return apiClient.get(`/api/attachments/${encodeURIComponent(attachmentId)}/download`, {
    responseType: 'blob',
  })
}

export function voidAttachment(attachmentId: string, reason: string): Promise<VoucherAttachmentItem> {
  return apiClient.post(`/api/attachments/${encodeURIComponent(attachmentId)}/void`, { reason })
}
