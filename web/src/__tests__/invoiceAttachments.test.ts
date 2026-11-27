/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'

import {
  attachmentTypeLabel,
  formatAttachmentSize,
  originalDocumentCellText,
  shortSha256,
  uploadOutcomeLabel,
  verifyStatusLabel,
} from '@/utils/invoiceAttachments'

describe('invoiceAttachments utils', () => {
  it('原件列文案：无附件为 —，有 XML 显示 XML✓，仅版式显示件数', () => {
    expect(originalDocumentCellText(0, false)).toBe('—')
    expect(originalDocumentCellText(undefined, undefined)).toBe('—')
    expect(originalDocumentCellText(1, true)).toBe('XML✓')
    expect(originalDocumentCellText(2, false)).toBe('2 件')
  })

  it('类型与结果标签：未知值回落 other / 原样', () => {
    expect(attachmentTypeLabel('einvoice_xml')).toBe('XML 原件')
    expect(attachmentTypeLabel('einvoice_ofd')).toBe('OFD 版式')
    expect(attachmentTypeLabel('unknown-type')).toBe('其他')
    expect(uploadOutcomeLabel('parked')).toBe('未匹配')
    expect(uploadOutcomeLabel('matched')).toBe('已挂接')
    expect(uploadOutcomeLabel('weird')).toBe('weird')
  })

  it('查验状态：只有 verified 显示已查验，空值/未查验都是未查验', () => {
    expect(verifyStatusLabel('verified')).toBe('已查验')
    expect(verifyStatusLabel('unverified')).toBe('未查验')
    expect(verifyStatusLabel(undefined)).toBe('未查验')
    expect(verifyStatusLabel('')).toBe('未查验')
  })

  it('sha256 只展示前 8 位；空串安全', () => {
    expect(shortSha256('a'.repeat(64))).toBe('a'.repeat(8))
    expect(shortSha256('')).toBe('')
  })

  it('附件大小格式化', () => {
    expect(formatAttachmentSize(0)).toBe('—')
    expect(formatAttachmentSize(512)).toBe('512 B')
    expect(formatAttachmentSize(2048)).toBe('2.0 KB')
    expect(formatAttachmentSize(5 * 1024 * 1024)).toBe('5.00 MB')
  })
})
