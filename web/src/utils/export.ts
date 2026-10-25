/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import * as XLSX from 'xlsx'
import type { Entry } from '@/api/types'
import type { LedgerBook } from '@/api/ledger'
import { formatDate } from './format'
import { directionLabel } from './ledgerFormat'
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

/**
 * 电子账簿导出：封面 + 科目余额表 + 总账 + 各科目明细账 + 日记账 + 序时账。
 *
 * 与页面同一份 payload，保证"导出的和打印的一致"。金额写成数字（Excel 里要能
 * 再算），余额不写负号而是拆成"方向 + 金额"两列——与账页口径一致。
 */
function excelDate(value: string | null | undefined): Date | string {
  // 不用 new Date('YYYY-MM-DD')：按 UTC 解析，东八区会退一天
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''))
  if (!match) return value || ''
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
}

export function exportLedgerBookToExcel(book: LedgerBook) {
  const workbook = XLSX.utils.book_new()
  const meta = book.meta
  const periodText = meta.scope === 'year' ? `${meta.year} 年度` : String(meta.month || '')
  const amount = (value: string | null | undefined) => (value ? Number(value) : 0)

  const cover = XLSX.utils.aoa_to_sheet([
    ['电子账簿'],
    [],
    ['账套名称', meta.companyName],
    ['账套期间', periodText],
    ['会计准则', meta.accountingStandard],
    ['编制人', meta.generatedBy || ''],
    ['打印日期', formatDate(new Date())],
    ['账簿编号', meta.bookNo || '（未签发）'],
    ['校验码', meta.contentHash ? meta.contentHash.slice(0, 16) : ''],
    ['生成系统', '梭罗记账 SoloBooks'],
  ])
  cover['!cols'] = [{ wch: 16 }, { wch: 44 }]
  XLSX.utils.book_append_sheet(workbook, cover, '封面')

  const balanceRows = book.balance.items.map((row) => ({
    科目编码: row.accountCode,
    科目名称: row.isParent ? `${row.accountName}（小计）` : row.accountName,
    期初方向: directionLabel(row.openingDirection),
    期初余额: amount(row.openingAbs),
    借方发生: amount(row.debitTotal),
    贷方发生: amount(row.creditTotal),
    期末方向: directionLabel(row.closingDirection),
    期末余额: amount(row.closingAbs),
  }))
  const balanceSheet = XLSX.utils.json_to_sheet(balanceRows, { cellDates: true })
  balanceSheet['!cols'] = [
    { wch: 12 }, { wch: 30 }, { wch: 8 }, { wch: 16 },
    { wch: 16 }, { wch: 16 }, { wch: 8 }, { wch: 16 },
  ]
  XLSX.utils.book_append_sheet(workbook, balanceSheet, '科目余额表')

  if (book.general && book.general.items.length) {
    const generalRows = book.general.items.flatMap((page) => [
      ...page.months.map((month) => ({
        科目编码: page.accountCode,
        科目名称: page.isParent ? `${page.accountName}（小计）` : page.accountName,
        月份: month.month,
        借方发生: amount(month.debit),
        贷方发生: amount(month.credit),
        借或贷: directionLabel(month.balanceDirection),
        余额: amount(month.balanceAbs),
      })),
      {
        科目编码: page.accountCode,
        科目名称: `${page.accountName}（本年合计）`,
        月份: '',
        借方发生: amount(page.totalDebit),
        贷方发生: amount(page.totalCredit),
        借或贷: directionLabel(page.closingDirection),
        余额: amount(page.closingAbs),
      },
    ])
    const generalSheet = XLSX.utils.json_to_sheet(generalRows, { cellDates: true })
    generalSheet['!cols'] = [
      { wch: 12 }, { wch: 30 }, { wch: 10 }, { wch: 16 },
      { wch: 16 }, { wch: 8 }, { wch: 16 },
    ]
    XLSX.utils.book_append_sheet(workbook, generalSheet, '总账')
  }

  for (const page of book.details) {
    const rows = [
      {
        日期: '',
        凭证号: '',
        摘要: '期初余额',
        对方科目: '',
        借方: '',
        贷方: '',
        借或贷: directionLabel(page.openingDirection),
        余额: amount(page.openingAbs),
      },
      ...page.lines.map((line) => ({
        日期: excelDate(line.date),
        凭证号: line.voucherRef || '',
        摘要: line.backfill ? `${line.summary}（补记）` : line.summary,
        对方科目: line.counterparties.map((item) => `${item.code} ${item.name}`).join('、'),
        借方: amount(line.debit) || '',
        贷方: amount(line.credit) || '',
        借或贷: directionLabel(line.balanceDirection),
        余额: amount(line.balanceAbs),
      })),
      {
        日期: '',
        凭证号: '',
        摘要: '本期合计',
        对方科目: '',
        借方: amount(page.periodTotals.debit),
        贷方: amount(page.periodTotals.credit),
        借或贷: directionLabel(page.closingDirection),
        余额: amount(page.closingAbs),
      },
      {
        日期: '',
        凭证号: '',
        摘要: '本年累计',
        对方科目: '',
        借方: amount(page.yearTotals.debit),
        贷方: amount(page.yearTotals.credit),
        借或贷: '',
        余额: '',
      },
    ]
    const sheet = XLSX.utils.json_to_sheet(rows, { cellDates: true })
    sheet['!cols'] = [
      { wch: 12 }, { wch: 18 }, { wch: 36 }, { wch: 32 },
      { wch: 14 }, { wch: 14 }, { wch: 8 }, { wch: 14 },
    ]
    // sheet 名最长 31 字符且不能含 : \ / ? * [ ]
    const rawName = `${page.code}${page.name}`.replace(/[:\\/?*[\]]/g, '').slice(0, 31)
    XLSX.utils.book_append_sheet(workbook, sheet, rawName || page.code)
  }

  for (const journal of book.journals) {
    if (!journal.rows.length) continue
    const rows = [
      {
        日期: '',
        摘要: '期初余额',
        凭证张数: '',
        借方: '',
        贷方: '',
        借或贷: directionLabel(journal.openingDirection),
        余额: amount(journal.openingAbs),
      },
      ...journal.rows.map((day) => ({
        日期: excelDate(day.date),
        摘要: day.summary,
        凭证张数: day.voucherCount,
        借方: amount(day.debit),
        贷方: amount(day.credit),
        借或贷: directionLabel(day.balanceDirection),
        余额: amount(day.balanceAbs),
      })),
      {
        日期: '',
        摘要: '本册合计',
        凭证张数: '',
        借方: amount(journal.totalDebit),
        贷方: amount(journal.totalCredit),
        借或贷: directionLabel(journal.closingDirection),
        余额: amount(journal.closingAbs),
      },
    ]
    const sheet = XLSX.utils.json_to_sheet(rows, { cellDates: true })
    sheet['!cols'] = [
      { wch: 12 }, { wch: 16 }, { wch: 10 }, { wch: 14 },
      { wch: 14 }, { wch: 8 }, { wch: 14 },
    ]
    XLSX.utils.book_append_sheet(workbook, sheet, journal.name)
  }

  if (book.chronological.items.length) {
    const rows = book.chronological.items.map((row) => ({
      日期: excelDate(row.date),
      凭证号: row.voucherRef || '',
      摘要: row.backfill ? `${row.summary}（补记）` : row.summary,
      科目编码: row.accountCode,
      科目名称: row.accountName,
      借方: amount(row.debit) || '',
      贷方: amount(row.credit) || '',
    }))
    const sheet = XLSX.utils.json_to_sheet(rows, { cellDates: true })
    sheet['!cols'] = [
      { wch: 12 }, { wch: 18 }, { wch: 36 }, { wch: 12 },
      { wch: 26 }, { wch: 14 }, { wch: 14 },
    ]
    XLSX.utils.book_append_sheet(workbook, sheet, '序时账')
  }

  // 目录带页码（F5）：页码来自服务端固定分页（pagination.sections），与打印的
  // PDF 页码一一对应，Excel 目录因此可以充当纸质册目录的对照页。
  const sheetNameOf: Record<string, string> = {}
  for (const name of workbook.SheetNames) sheetNameOf[name] = name
  const sectionRows = (book.pagination?.sections || []).map((section) => {
    let sheetName = ''
    if (section.kind === 'balance') sheetName = '科目余额表'
    else if (section.kind === 'general') sheetName = '总账'
    else if (section.kind === 'chronological') sheetName = '序时账'
    else if (section.kind === 'journal') {
      const journal = book.journals.find((item) => item.kind === section.kind_code)
      sheetName = journal ? journal.name : ''
    } else if (section.kind === 'detail') {
      const detail = book.details.find((item) => item.code === section.code)
      sheetName = detail ? `${detail.code}${detail.name}`.replace(/[:\\/?*[\]]/g, '').slice(0, 31) : ''
    }
    if (sheetName && !sheetNameOf[sheetName]) sheetName = '' // 防御：section 与实际 sheet 对不上时不写错页码
    return {
      起始页: section.pageNo,
      页数: section.pageCount,
      章节: section.title,
      工作表: sheetName || '（纸质册专用，无工作表）',
    }
  })
  const tocSheet = XLSX.utils.json_to_sheet(sectionRows)
  tocSheet['!cols'] = [{ wch: 8 }, { wch: 8 }, { wch: 40 }, { wch: 34 }]
  XLSX.utils.book_append_sheet(workbook, tocSheet, '目录')
  workbook.SheetNames = ['目录', ...workbook.SheetNames.filter((name) => name !== '目录')]

  const suffix = meta.scope === 'year' ? `${meta.year}年度` : String(meta.month || '')
  XLSX.writeFile(workbook, `电子账簿_${meta.companyName}_${suffix}.xlsx`)
}
