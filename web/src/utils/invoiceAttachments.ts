/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
 */

/**
 * 发票电子原件（attachments）的派生状态。
 *
 * 台账「原件」列与附件抽屉共用同一套标签/判定（AGENTS.md A3：展示与账务同源，
 * 派生状态收敛到 utils 并补单测，不在页面里另起一套）。
 */

export type InvoiceAttachmentDocumentType = 'einvoice_xml' | 'einvoice_pdf' | 'einvoice_ofd' | 'other'

export const INVOICE_ATTACHMENT_TYPE_LABELS: Record<InvoiceAttachmentDocumentType, string> = {
  einvoice_xml: 'XML 原件',
  einvoice_pdf: 'PDF 版式',
  einvoice_ofd: 'OFD 版式',
  other: '其他',
}

export type InvoiceUploadOutcome = 'matched' | 'parked' | 'stored' | 'duplicate' | 'rejected'

export const INVOICE_UPLOAD_OUTCOME_LABELS: Record<InvoiceUploadOutcome, string> = {
  matched: '已挂接',
  parked: '未匹配',
  stored: '已留存',
  duplicate: '重复跳过',
  rejected: '已拒收',
}

export type InvoiceVerifyStatus = 'verified' | 'unverified'

export const INVOICE_VERIFY_STATUS_LABELS: Record<InvoiceVerifyStatus, string> = {
  verified: '已查验',
  unverified: '未查验',
}

/** 原件上传入口允许的扩展名（与后端 SUPPORTED_EXTS 同口径） */
export const INVOICE_ATTACHMENT_ACCEPT = '.xml,.zip,.pdf,.ofd'

/** 补传版式文件时只收版式/XML，不接受 zip（zip 是批量原件入口） */
export const INVOICE_SUPPLEMENT_ACCEPT = '.xml,.pdf,.ofd'

export function isInvoiceAttachmentDocumentType(value: string): value is InvoiceAttachmentDocumentType {
  return value in INVOICE_ATTACHMENT_TYPE_LABELS
}

export function attachmentTypeLabel(value: string): string {
  return isInvoiceAttachmentDocumentType(value) ? INVOICE_ATTACHMENT_TYPE_LABELS[value] : INVOICE_ATTACHMENT_TYPE_LABELS.other
}

export function uploadOutcomeLabel(value: string): string {
  return value in INVOICE_UPLOAD_OUTCOME_LABELS
    ? INVOICE_UPLOAD_OUTCOME_LABELS[value as InvoiceUploadOutcome]
    : value
}

export function verifyStatusLabel(value: string | undefined | null): string {
  return value === 'verified'
    ? INVOICE_VERIFY_STATUS_LABELS.verified
    : INVOICE_VERIFY_STATUS_LABELS.unverified
}

/** 台账「原件」列的主文案：有 XML 原件 → 「XML✓」，仅有版式 → 「N 件」，无 → 「—」 */
export function originalDocumentCellText(attachmentCount: number | undefined, hasOriginalXml: boolean | undefined): string {
  const count = attachmentCount || 0
  if (count === 0) return '—'
  return hasOriginalXml ? 'XML✓' : `${count} 件`
}

/** sha256 展示前 8 位（对齐电子账簿 pdfSha256 的展示惯例） */
export function shortSha256(sha: string): string {
  return sha ? sha.slice(0, 8) : ''
}

export function formatAttachmentSize(sizeBytes: number): string {
  if (!sizeBytes || sizeBytes <= 0) return '—'
  if (sizeBytes < 1024) return `${sizeBytes} B`
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`
  return `${(sizeBytes / 1024 / 1024).toFixed(2)} MB`
}

export interface UploadOutcomeLike {
  fileName: string
  outcome: string
  error?: string
}

/** 上传结果聚合成一条用户可读摘要（命中/暂存/留存/重复/拒收 + 首条原因） */
export function summarizeUploadResults(results: UploadOutcomeLike[]): string {
  if (results.length === 0) return ''
  const buckets = new Map<string, number>()
  for (const result of results) {
    buckets.set(result.outcome, (buckets.get(result.outcome) || 0) + 1)
  }
  const parts: string[] = []
  const order: InvoiceUploadOutcome[] = ['matched', 'parked', 'stored', 'duplicate', 'rejected']
  for (const outcome of order) {
    const count = buckets.get(outcome)
    if (count) parts.push(`${INVOICE_UPLOAD_OUTCOME_LABELS[outcome]} ${count} 份`)
  }
  const firstRejected = results.find(result => result.outcome === 'rejected' && result.error)
  let message = parts.length > 0 ? `原件处理完成：${parts.join('，')}` : ''
  if (firstRejected?.error) {
    message += `；「${firstRejected.fileName}」${firstRejected.error}`
  }
  return message
}
