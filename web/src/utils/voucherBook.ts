/**
 * Copyright (c) 2026 芷子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
 */

import type {
  VoucherBook,
  VoucherBookOriginalItem,
  VoucherBookVoucher,
  VoucherBookVoucherAttachment,
} from '@/api/voucherBook'

/**
 * 凭证册（voucher book）派生状态。台账/凭证册页与预览共用同一套标签与判定
 * （AGENTS.md A3：派生状态收敛到 utils 并补单测）。
 */

export type VoucherBookStatus = 'issued' | 'void'

export const VOUCHER_BOOK_STATUS_LABELS: Record<VoucherBookStatus, string> = {
  issued: '已签发',
  void: '已作废',
}

export function voucherBookStatusLabel(status: string | null | undefined): string {
  return status === 'void' ? VOUCHER_BOOK_STATUS_LABELS.void : VOUCHER_BOOK_STATUS_LABELS.issued
}

/** 签发条件：期间已关账且当前无有效签发（作废过的册不拦重签） */
export function canIssueVoucherBook(
  periodStatus: string | undefined,
  latestIssued: { status: string } | null | undefined,
): boolean {
  return periodStatus === 'closed' && (!latestIssued || latestIssued.status === 'void')
}

/** 附件原件类型（凭证册目录用，与 utils/invoiceAttachments 及后端 _TYPE_LABELS 同口径） */
export function voucherOriginalTypeLabel(documentType: string): string {
  const labels: Record<string, string> = {
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
  return labels[documentType] || '其他'
}

/** 凭证册预览下载文件名（blob 直通拿不到 Content-Disposition，前端拼） */
export function voucherBookFileName(month: string, suffix: '预览件' | string, bookNo?: string): string {
  const label = bookNo ? `册号${bookNo}` : suffix || '预览件'
  return `凭证册_${month}_${label}.pdf`
}

/* ── 仿真册翻页：页面数组（与后端 pagination 真值同源） ── */

/** 原件目录每页行数（与后端 voucher_book_service._paginate 同一公式） */
export const ORIGINAL_ROWS_PER_PAGE = 12
/** 自动翻页间隔 */
export const AUTO_FLIP_INTERVAL_MS = 5000
/** 单页翻转动画时长（与 VoucherBookViewer 的真实翻书卷页动画一致） */
export const FLIP_DURATION_MS = 620
export const MIN_ZOOM = 0.6
export const MAX_ZOOM = 1.6
/** 仿真册标准 A4 横版设计画布尺寸（用于等比缩放，锁定不同视口下的版式比例） */
export const BOOK_CANVAS_WIDTH = 1120
export const BOOK_CANVAS_HEIGHT = 792

export type BookPageKind = 'cover' | 'voucher' | 'invoice' | 'attachment' | 'originals' | 'signoff'

export type BookPage =
  | { kind: 'cover'; pageNo: number }
  | { kind: 'voucher'; pageNo: number; voucher: VoucherBookVoucher }
  | { kind: 'invoice'; pageNo: number; voucher: VoucherBookVoucher; attachment: VoucherBookVoucherAttachment }
  | { kind: 'attachment'; pageNo: number; voucher: VoucherBookVoucher; attachment: VoucherBookVoucherAttachment }
  | { kind: 'originals'; pageNo: number; rows: VoucherBookOriginalItem[]; startIndex: number; totalRows: number }
  | { kind: 'signoff'; pageNo: number }

export const BOOK_SECTION_LABELS: Record<BookPageKind, string> = {
  cover: '凭证封面',
  voucher: '记账凭证',
  invoice: '发票复现',
  attachment: '原始凭证',
  originals: '原件目录',
  signoff: '装订签章',
}

const SECTION_OF_KIND: Record<BookPageKind, 'cover' | 'vouchers' | 'originals' | 'signoff'> = {
  cover: 'cover',
  voucher: 'vouchers',
  invoice: 'vouchers',
  attachment: 'vouchers',
  originals: 'originals',
  signoff: 'signoff',
}

/** 单页顺序：封面 → 每凭证（凭证页 + XML/图片附件页）→ 原件目录 → 签章 */
export function flattenVoucherBookPages(payload: VoucherBook): BookPage[] {
  const pages: BookPage[] = []
  pages.push({ kind: 'cover', pageNo: 1 })

  for (const voucher of payload.vouchers || []) {
    pages.push({ kind: 'voucher', pageNo: pages.length + 1, voucher })
    for (const attachment of voucher.attachments || []) {
      if (attachment.documentType === 'einvoice_xml' && attachment.parsed) {
        pages.push({ kind: 'invoice', pageNo: pages.length + 1, voucher, attachment })
      } else {
        pages.push({ kind: 'attachment', pageNo: pages.length + 1, voucher, attachment })
      }
    }
  }

  const originals = payload.originals || []
  const originalPages =
    payload.pagination?.originalPages || Math.max(1, Math.ceil(originals.length / ORIGINAL_ROWS_PER_PAGE))
  for (let index = 0; index < originalPages; index += 1) {
    pages.push({
      kind: 'originals',
      pageNo: pages.length + 1,
      rows: originals.slice(index * ORIGINAL_ROWS_PER_PAGE, (index + 1) * ORIGINAL_ROWS_PER_PAGE),
      startIndex: index * ORIGINAL_ROWS_PER_PAGE,
      totalRows: originals.length,
    })
  }

  pages.push({ kind: 'signoff', pageNo: pages.length + 1 })
  return pages
}

/** 后端分页真值（阅读器页脚与跳转都以此为准，不用 DOM 页数反推） */
export function voucherBookTotalPages(payload: VoucherBook): number {
  return payload.pagination?.totalPages || flattenVoucherBookPages(payload).length
}

/** 某节第一页在 pages 数组中的下标（1-based 页码 → 索引 = 页码-1） */
export function firstPageIndexOfSection(pages: BookPage[], sectionKey: string): number {
  const targetKind = (Object.keys(SECTION_OF_KIND) as BookPageKind[]).find(
    (kind) => SECTION_OF_KIND[kind] === sectionKey,
  )
  if (!targetKind) return 0
  const index = pages.findIndex((page) => page.kind === targetKind)
  return index >= 0 ? index : 0
}

/** 阅读器缩放夹取（0.6–1.6），非数字回落 1 */
export function clampZoom(value: number): number {
  if (!Number.isFinite(value)) return 1
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round(value * 100) / 100))
}

/** 冻结 payload 的凭证 → VoucherSheet 需要的 entry 形状（book.vue 与仿真册共用） */
export function voucherSheetEntry(voucher: VoucherBookVoucher): Record<string, unknown> {
  const group = (side: 'debit' | 'credit') =>
    voucher.lines
      .filter((line) => line.side === side)
      .map((line) => ({
        code: line.code,
        name: line.name,
        amount: line.amount,
        auxiliary: line.auxiliary,
        quantity: line.quantity,
        unit: line.unit,
        unitPrice: line.unitPrice,
      }))
  return {
    title: voucher.title,
    voucherRef: voucher.voucherNo,
    occurDate: voucher.date,
    voucherLines: { debit: group('debit'), credit: group('credit') },
  }
}
