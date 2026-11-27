/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  hasInvoiceColumns,
  invoiceFileRejection,
  looksLikeStatementColumns,
} from '@/utils/invoiceImport'

// 杭州银行交易明细报表的真实表头（2026-11 被误传到发票导入）
const BANK_COLUMNS = [
  '序号', '己方银行账号', '别名', '己方银行户名', '对手方银行户名',
  '对手方银行账号', '己方开户行', '对手方开户名', '支出金额（元）',
  '收入金额（元）', '交易时间', '账户余额（元）', '摘要', '附言',
]

const TAX_BUREAU_COLUMNS = [
  '数电发票号码', '开票日期', '销方名称', '销方识别号', '购买方名称',
  '购方识别号', '金额', '税额', '价税合计', '税率', '发票状态',
]

const OTHER_INVOICE_COLUMNS = ['发票代码', '发票号码', '开票日期', '销售方', '购买方', '价税合计']

describe('发票导入文件识别', () => {
  it('银行流水表头被识别为流水', () => {
    expect(looksLikeStatementColumns(BANK_COLUMNS)).toBe(true)
    expect(hasInvoiceColumns(BANK_COLUMNS)).toBe(false)
    expect(invoiceFileRejection(BANK_COLUMNS)).toContain('银行流水')
  })

  it('税务发票导出和普通发票清单都能通过', () => {
    expect(looksLikeStatementColumns(TAX_BUREAU_COLUMNS)).toBe(false)
    expect(hasInvoiceColumns(TAX_BUREAU_COLUMNS)).toBe(true)
    expect(invoiceFileRejection(TAX_BUREAU_COLUMNS)).toBeNull()

    expect(invoiceFileRejection(OTHER_INVOICE_COLUMNS)).toBeNull()
  })

  it('没有发票字段的普通表格在结账向导被拦，台账映射向导仍允许手动映射', () => {
    const generic = ['项目', '金额', '备注']
    expect(invoiceFileRejection(generic)).toContain('不像税务发票明细')
    expect(invoiceFileRejection(generic, { allowManualMapping: true })).toBeNull()
  })
})
