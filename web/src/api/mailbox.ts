/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/** 邮件单据通道：专属收件地址 + 收件箱附件暂存 */

import apiClient from './client'

export interface MailboxInfo {
  enabled: boolean
  addressCode: string
  domain: string
  address: string
}

export type InboxImportKind = 'invoice' | 'bank' | 'wechat_bill' | 'unknown'
export type InboxStatus = 'pending' | 'consumed' | 'dismissed' | 'unsupported'

export interface InboxDocumentItem {
  id: string
  filename: string
  fileExt: string
  sizeBytes: number
  importKind: InboxImportKind
  status: InboxStatus
  note: string
  source: string
  emailFrom: string
  emailSubject: string
  receivedAt: string | null
  consumedAt: string | null
}

export function fetchMailbox(): Promise<MailboxInfo> {
  return apiClient.get('/api/mailbox')
}

export function resetMailboxAddress(): Promise<MailboxInfo> {
  return apiClient.post('/api/mailbox/address/reset')
}

export function fetchInboxDocuments(status?: InboxStatus): Promise<{ items: InboxDocumentItem[] }> {
  return apiClient.get('/api/mailbox/documents', { params: status ? { status } : {} })
}

/** 附件内容（二进制）。下载与「构造 File 走现有导入向导」共用此端点。 */
export function fetchInboxDocumentFile(id: string): Promise<Blob> {
  return apiClient.get(`/api/mailbox/documents/${id}/file`, { responseType: 'blob' })
}

export function markInboxDocumentConsumed(id: string): Promise<InboxDocumentItem> {
  return apiClient.post(`/api/mailbox/documents/${id}/consumed`)
}

export function deleteInboxDocument(id: string): Promise<{ deleted: boolean }> {
  return apiClient.delete(`/api/mailbox/documents/${id}`)
}
