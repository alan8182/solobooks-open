/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

/** 凭证册快照（ledger_book_snapshots 表，bookKind='voucher'） */
export interface VoucherBookSnapshotSummary {
  id: string
  bookKind: 'voucher'
  period: string
  bookNo: string
  status: 'issued' | 'void'
  contentHash: string
  pageCount: number
  lineCount: number
  anchor: Record<string, unknown>
  generatedBy: string | null
  generatedAt: string | null
  hasPdf?: boolean
  pdfSha256?: string | null
  pdfSizeBytes?: number | null
  pdfGeneratedAt?: string | null
  voidReason: string | null
  voidedBy: string | null
  voidedAt: string | null
}

/** 单张记账凭证（build_voucher_book payload，与 VoucherSheet 版式字段对应） */
export interface VoucherBookVoucherLine {
  summary: string
  code: string
  name: string
  auxiliaryText: string
  /** 原辅助核算对象（屏幕预览的 VoucherSheet 消费） */
  auxiliary?: Record<string, unknown>
  side: 'debit' | 'credit'
  amount: string
  quantity: string
  unit: string
  unitPrice: string
}

export interface VoucherBookVoucher {
  entryId: string
  voucherNo: string
  voucherSeq: string
  date: string
  period: string
  title: string
  backfill: boolean
  lines: VoucherBookVoucherLine[]
  blankRows: number
  debitTotal: string
  creditTotal: string
  capitalAmount: string
  attachmentCount: number
  invoiceNumbers: string[]
  /** XML 原件解析结果：凭证后逐份渲染发票复现页（方案 A） */
  attachments?: VoucherBookVoucherAttachment[]
}

export interface VoucherBookOriginalItem {
  attachmentId: string
  entryId: string
  voucherNo: string
  invoiceNumber: string
  fileName: string
  documentType: string
  contentSha256: string
  sizeBytes: number
}

/** XML 原件解析结果（凭证后发票复现页的数据源，与后端 attachments.parsed_json 同源） */
export interface VoucherBookInvoiceParsed {
  invoiceNumber: string
  issueTime: string
  generalOrSpecial: string
  isBlue: boolean
  hasTaxBureauSignature: boolean
  buyer: { name: string; taxId: string }
  seller: { name: string; taxId: string }
  amounts: { excl: string; tax: string; incl: string; inclChinese: string }
  drawer?: string
  specific?: Record<string, { fields?: Record<string, string> }>
  items: Array<{
    itemName: string
    spec: string
    unit: string
    quantity: string
    unitPrice: string
    amount: string
    taxRate: string
    taxAmount: string
  }>
  remark?: string
}

export interface VoucherBookVoucherAttachment {
  attachmentId: string
  invoiceNumber: string
  issueTime?: string
  documentType: string
  contentSha256: string
  fileName: string
  fileExt?: string
  storedPath?: string
  parsedMeta?: Record<string, unknown> | null
  parsed?: VoucherBookInvoiceParsed | null
}

export interface VoucherBook {
  meta: {
    companyName: string
    month: string
    periodStart: string
    periodEnd: string
    periodStatus: string
    voucherCount: number
    attachmentTotal: number
    xmlOriginalTotal: number
    debitTotal: string
    contentHash?: string
    bookNo?: string
    generatedAt?: string
    generatedBy?: string
    pageCount?: number
    voucherRange?: { first: string; last: string }
  }
  pagination: {
    sections: Array<{ key: string; title: string; pages: number }>
    totalPages: number
    originalPages: number
  }
  vouchers: VoucherBookVoucher[]
  originals: VoucherBookOriginalItem[]
  originalsMissing: Array<{ voucherNo: string; title: string; date: string }>
}

export interface VoucherBookSnapshotDetail extends VoucherBookSnapshotSummary {
  payload: VoucherBook
}

/** 仿真册预览 payload（只读，不落库；同内容已签发时 signed=true） */
export function getVoucherBookPreview(month: string): Promise<{
  payload: VoucherBook
  signed: boolean
  bookNo: string | null
  status: string | null
}> {
  return apiClient.get('/api/voucher-book/preview', { params: { month } })
}

export function issueVoucherBookSnapshot(month: string): Promise<VoucherBookSnapshotDetail> {
  return apiClient.post('/api/voucher-book/snapshots', { month })
}

/** 期间关账状态（签发条可用性；同一 AccountingPeriod 真相） */
export function getVoucherBookPeriodStatus(month: string): Promise<{ month: string; periodStatus: string }> {
  return apiClient.get('/api/voucher-book/period-status', { params: { month } })
}

export function listVoucherBookSnapshots(period?: string): Promise<{ items: VoucherBookSnapshotSummary[] }> {
  return apiClient.get('/api/voucher-book/snapshots', { params: period ? { period } : {} })
}

export function getVoucherBookSnapshot(id: string): Promise<VoucherBookSnapshotDetail> {
  return apiClient.get(`/api/voucher-book/snapshots/${id}`)
}

export function voidVoucherBookSnapshot(id: string, reason: string): Promise<VoucherBookSnapshotSummary> {
  return apiClient.post(`/api/voucher-book/snapshots/${id}/void`, { reason })
}

/** 预览/复用已签发件：blob 直通，文件名由调用方按响应内容拼 */
export function downloadVoucherBookPdf(month: string): Promise<Blob> {
  return apiClient.get('/api/voucher-book/pdf', {
    params: { month },
    responseType: 'blob',
    timeout: 120000,
  }) as unknown as Promise<Blob>
}

export function downloadVoucherBookSnapshotPdf(snapshotId: string): Promise<Blob> {
  return apiClient.get(`/api/voucher-book/snapshots/${snapshotId}/download`, {
    responseType: 'blob',
    timeout: 120000,
  }) as unknown as Promise<Blob>
}

/** 凭证 → 真实附单据数（打印页/详情页补数用；凭证册与打印页共用同一真相） */
export function getAttachmentCountsByEntries(entryIds: string[]): Promise<{ counts: Record<string, number> }> {
  return apiClient.get('/api/voucher-book/attachment-counts', {
    params: { entry_ids: entryIds.join(',') },
  })
}
