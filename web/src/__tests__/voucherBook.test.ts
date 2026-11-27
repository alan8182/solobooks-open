/**
 * Copyright (c) 2026 芷子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'

import {
  canIssueVoucherBook,
  voucherBookFileName,
  voucherBookStatusLabel,
  voucherOriginalTypeLabel,
} from '@/utils/voucherBook'

describe('voucherBook utils', () => {
  it('状态标签：void 显示已作废，其余（含空值）显示已签发', () => {
    expect(voucherBookStatusLabel('issued')).toBe('已签发')
    expect(voucherBookStatusLabel('void')).toBe('已作废')
    expect(voucherBookStatusLabel(undefined)).toBe('已签发')
  })

  it('签发条件：必须已关账且无有效签发', () => {
    expect(canIssueVoucherBook('closed', null)).toBe(true)
    expect(canIssueVoucherBook('closed', undefined)).toBe(true)
    expect(canIssueVoucherBook('closed', { status: 'void' })).toBe(true)
    expect(canIssueVoucherBook('closed', { status: 'issued' })).toBe(false)
    expect(canIssueVoucherBook('open', null)).toBe(false)
    expect(canIssueVoucherBook(undefined, null)).toBe(false)
  })

  it('原件类型标签与 invoiceAttachments 同口径', () => {
    expect(voucherOriginalTypeLabel('einvoice_xml')).toBe('XML 原件')
    expect(voucherOriginalTypeLabel('unknown')).toBe('其他')
  })

  it('下载文件名：有册号用册号，否则用后缀', () => {
    expect(voucherBookFileName('2026-10', '预览件')).toBe('凭证册_2026-10_预览件.pdf')
    expect(voucherBookFileName('2026-10', '预览件', '2026-10-0001')).toBe('凭证册_2026-10_册号2026-10-0001.pdf')
  })
})
