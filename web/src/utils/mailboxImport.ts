/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 收件箱附件 → File 的唯一口径：拉取 bytes 并按原始文件名构造 File，
 * 之后与手动上传完全同一条解析确认管线（发票/银行/微信账单向导）。
 */

import { fetchInboxDocumentFile, type InboxDocumentItem, type InboxImportKind } from '@/api/mailbox'

const MIME_BY_EXT: Record<string, string> = {
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  xls: 'application/vnd.ms-excel',
  csv: 'text/csv',
}

export async function inboxDocumentToFile(doc: Pick<InboxDocumentItem, 'id' | 'filename' | 'fileExt'>): Promise<File> {
  const blob = await fetchInboxDocumentFile(doc.id)
  const mime = MIME_BY_EXT[doc.fileExt?.toLowerCase()] || 'application/octet-stream'
  return new File([blob], doc.filename, { type: mime })
}

export const IMPORT_KIND_LABELS: Record<InboxImportKind, string> = {
  invoice: '发票',
  bank: '银行流水',
  wechat_bill: '微信账单',
  unknown: '未识别类型',
}

/** 单据类型中文名（收件箱列表与导入入口共用，避免两处文案漂移） */
export function importKindLabel(kind: InboxImportKind): string {
  return IMPORT_KIND_LABELS[kind] || '未识别类型'
}
