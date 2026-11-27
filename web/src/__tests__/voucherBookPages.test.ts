/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'

import type { VoucherBook, VoucherBookVoucher } from '@/api/voucherBook'
import {
  AUTO_FLIP_INTERVAL_MS,
  MAX_ZOOM,
  MIN_ZOOM,
  clampZoom,
  firstPageIndexOfSection,
  flattenVoucherBookPages,
  voucherBookTotalPages,
  voucherOriginalTypeLabel,
  voucherSheetEntry,
} from '@/utils/voucherBook'

function makeVoucher(seq: number, attachmentCount = 0): VoucherBookVoucher {
  return {
    entryId: `e-${seq}`,
    voucherNo: `记-${String(seq).padStart(4, '0')}`,
    voucherSeq: String(seq),
    date: `2026-10-${String(seq).padStart(2, '0')}`,
    period: '2026-10-01',
    title: `测试业务${seq}`,
    backfill: false,
    lines: [
      {
        summary: `测试业务${seq}`,
        code: '5602013',
        name: '管理费用-租赁费',
        auxiliaryText: '往来:测试供应商',
        auxiliary: { partner_name: '测试供应商' },
        side: 'debit',
        amount: '106.00',
        quantity: '',
        unit: '',
        unitPrice: '',
      },
      {
        summary: `测试业务${seq}`,
        code: '1002',
        name: '银行存款',
        auxiliaryText: '',
        side: 'credit',
        amount: '106.00',
        quantity: '',
        unit: '',
        unitPrice: '',
      },
    ],
    blankRows: 4,
    debitTotal: '106.00',
    creditTotal: '106.00',
    capitalAmount: '壹佰零陆圆整',
    attachmentCount,
    invoiceNumbers: attachmentCount > 0 ? [`2633700000000000000${seq}`] : [],
    attachments: Array.from({ length: attachmentCount }, (_, index) => ({
      attachmentId: `att-${seq}-${index}`,
      invoiceNumber: `2633700000000000000${seq}`,
      issueTime: '2026-10-03',
      documentType: 'einvoice_xml',
      contentSha256: 'c'.repeat(64),
      fileName: `dzfp_${seq}.xml`,
      parsed: {
        invoiceNumber: `2633700000000000000${seq}`,
        issueTime: '2026-10-03',
        generalOrSpecial: '普通发票',
        isBlue: true,
        hasTaxBureauSignature: true,
        buyer: { name: '示例采购有限公司', taxId: '91330110MA00BUYER1' },
        seller: { name: '测试云计算有限公司', taxId: '91330106MA00TEST01' },
        amounts: { excl: '100.00', tax: '6.00', incl: '106.00', inclChinese: '壹佰零陆圆整' },
        items: [],
      },
    })),
  }
}

function makePayload(voucherAttachmentCounts: number[], originalCount: number): VoucherBook {
  const vouchers = voucherAttachmentCounts.map((count, index) => makeVoucher(index + 1, count))
  const originals = Array.from({ length: originalCount }, (_, index) => ({
    attachmentId: `att-o-${index}`,
    entryId: 'e-1',
    voucherNo: '记-0001',
    invoiceNumber: `2633700000000000000${index}`,
    fileName: `file-${index}.xml`,
    documentType: 'einvoice_xml',
    contentSha256: 'd'.repeat(64),
    sizeBytes: 4096,
  }))
  const voucherPages = voucherAttachmentCounts.reduce((sum, count) => sum + 1 + count, 0)
  const originalPages = Math.max(1, Math.ceil(originalCount / 12)) || 1
  return {
    meta: {
      companyName: '示例采购有限公司',
      month: '2026-10',
      periodStart: '2026-10-01',
      periodEnd: '2026-10-31',
      periodStatus: 'closed',
      voucherCount: vouchers.length,
      attachmentTotal: voucherAttachmentCounts.reduce((a, b) => a + b, 0),
      xmlOriginalTotal: originalCount,
      debitTotal: '212.00',
      contentHash: 'a'.repeat(64),
    },
    pagination: {
      sections: [
        { key: 'cover', title: '凭证册封面', pages: 1 },
        { key: 'vouchers', title: '记账凭证', pages: voucherPages },
        { key: 'originals', title: '原件归档目录', pages: originalPages },
        { key: 'signoff', title: '装订签章', pages: 1 },
      ],
      totalPages: 1 + voucherPages + originalPages + 1,
      originalPages,
    },
    vouchers,
    originals,
    originalsMissing: [],
  }
}

describe('voucherBook 仿真册分页', () => {
  it('页序：封面 → 每凭证（凭证页+附件复现页）→ 原件目录 → 签章，且与分页真值一致', () => {
    const payload = makePayload([2, 0], 13)
    const pages = flattenVoucherBookPages(payload)
    expect(pages.map((page) => page.kind)).toEqual([
      'cover',
      'voucher',
      'invoice',
      'invoice',
      'voucher',
      'originals',
      'originals',
      'signoff',
    ])
    expect(pages.map((page) => page.pageNo)).toEqual([1, 2, 3, 4, 5, 6, 7, 8])
    expect(voucherBookTotalPages(payload)).toBe(8)
    expect(pages.length).toBe(payload.pagination.totalPages)
  })

  it('原件目录按 12 行分页，末页行数正确', () => {
    const pages = flattenVoucherBookPages(makePayload([1], 13))
    const originals = pages.filter((page) => page.kind === 'originals')
    expect(originals).toHaveLength(2)
    expect(originals[0].kind === 'originals' && originals[0].rows).toHaveLength(12)
    expect(originals[1].kind === 'originals' && originals[1].rows).toHaveLength(1)
    expect(originals[1].kind === 'originals' && originals[1].startIndex).toBe(12)
  })

  it('空附件月：凭证后不插发票页，总页数仍与后端一致', () => {
    const payload = makePayload([0, 0], 0)
    const pages = flattenVoucherBookPages(payload)
    expect(pages.filter((page) => page.kind === 'invoice')).toHaveLength(0)
    expect(pages.length).toBe(payload.pagination.totalPages)
  })

  it('节跳转取该节首页；缩放夹取在 0.6–1.6', () => {
    const payload = makePayload([1], 2)
    const pages = flattenVoucherBookPages(payload)
    expect(firstPageIndexOfSection(pages, 'cover')).toBe(0)
    expect(firstPageIndexOfSection(pages, 'vouchers')).toBe(1)
    expect(firstPageIndexOfSection(pages, 'originals')).toBe(3)
    expect(firstPageIndexOfSection(pages, 'signoff')).toBe(4)
    expect(clampZoom(0.2)).toBe(MIN_ZOOM)
    expect(clampZoom(3)).toBe(MAX_ZOOM)
    expect(clampZoom(Number.NaN)).toBe(1)
    expect(AUTO_FLIP_INTERVAL_MS).toBe(5000)
  })

  it('voucherSheetEntry 按借/贷分组并保留辅助核算', () => {
    const entry = voucherSheetEntry(makeVoucher(1)) as {
      voucherLines: { debit: Array<Record<string, unknown>>; credit: Array<Record<string, unknown>> }
      voucherRef: string
    }
    expect(entry.voucherRef).toBe('记-0001')
    expect(entry.voucherLines.debit).toHaveLength(1)
    expect(entry.voucherLines.credit).toHaveLength(1)
    expect(entry.voucherLines.debit[0].auxiliary).toEqual({ partner_name: '测试供应商' })
  })

  it('银行回单/支付截图等非 XML 附件展平为 attachment 页并支持中文类型标签', () => {
    const payload = makePayload([0], 0)
    payload.vouchers[0].attachmentCount = 2
    payload.vouchers[0].attachments = [
      {
        attachmentId: 'att-bank-1',
        invoiceNumber: '',
        documentType: 'bank_receipt',
        fileExt: 'png',
        contentSha256: 'e'.repeat(64),
        fileName: '银行回单_2026-10-12.png',
        parsedMeta: { receiptNo: 'REC001', amount: '300.00' },
      },
      {
        attachmentId: 'att-pay-1',
        invoiceNumber: '',
        documentType: 'payment_screenshot',
        fileExt: 'png',
        contentSha256: 'f'.repeat(64),
        fileName: '微信支付.png',
      },
    ]
    const pages = flattenVoucherBookPages(payload)
    const kinds = pages.filter((p) => p.kind === 'attachment')
    expect(kinds).toHaveLength(2)
    expect(voucherOriginalTypeLabel('bank_receipt')).toBe('银行回单')
    expect(voucherOriginalTypeLabel('bank_statement_pdf')).toBe('银行对账单')
    expect(voucherOriginalTypeLabel('payment_screenshot')).toBe('支付截图')
    expect(voucherOriginalTypeLabel('reimbursement_form')).toBe('报销单')
    expect(voucherOriginalTypeLabel('payroll_sheet')).toBe('工资表')
    expect(voucherOriginalTypeLabel('contract')).toBe('合同')
  })
})
