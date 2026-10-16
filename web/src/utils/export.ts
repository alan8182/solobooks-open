/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import * as XLSX from 'xlsx'
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'
import type { Entry } from '@/api/types'
import { formatDate } from './format'
import { getEntryNaturePresentation } from './entryNature'

export function exportLedgerToExcel(entries: Entry[], filename?: string) {
  const data = entries.map(e => ({
    '日期': e.occurDate ? formatDate(e.occurDate) : '',
    '标题': e.title,
    '分类': e.category,
    '方向': getEntryNaturePresentation(e.direction, e.category).label,
    '金额': e.amount,
    '支付方式': e.payer === 'company_account' ? '公司账户' : e.payer === 'boss_personal' ? '股东个人' : '',
    '发票类型': e.invoiceType || '',
    '发票号码': e.invoiceNumber || '',
    '商家': e.merchant || '',
    '状态': e.voucherStatus === 'draft' ? '草稿' : e.voucherStatus === 'posted' ? '已过账' : '',
  }))

  const ws = XLSX.utils.json_to_sheet(data)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '账本')

  const colWidths = Object.keys(data[0] || {}).map(k => ({ wch: Math.max(k.length * 2, 14) }))
  ws['!cols'] = colWidths

  XLSX.writeFile(wb, filename || `账本_${formatDate(new Date())}.xlsx`)
}

export async function exportReportToPDF(elementId: string, filename?: string) {
  const el = document.getElementById(elementId)
  if (!el) throw new Error(`Element #${elementId} not found`)

  const canvas = await html2canvas(el, {
    scale: 2,
    useCORS: true,
    backgroundColor: '#ffffff',
  })

  const imgData = canvas.toDataURL('image/png')
  const pdf = new jsPDF('p', 'mm', 'a4')
  const pdfWidth = pdf.internal.pageSize.getWidth()
  const pdfHeight = (canvas.height * pdfWidth) / canvas.width

  let heightLeft = pdfHeight
  let position = 0
  const pageHeight = pdf.internal.pageSize.getHeight()

  pdf.addImage(imgData, 'PNG', 0, position, pdfWidth, pdfHeight)
  heightLeft -= pageHeight

  while (heightLeft > 0) {
    position -= pageHeight
    pdf.addPage()
    pdf.addImage(imgData, 'PNG', 0, position, pdfWidth, pdfHeight)
    heightLeft -= pageHeight
  }

  pdf.save(filename || `报表_${formatDate(new Date())}.pdf`)
}
