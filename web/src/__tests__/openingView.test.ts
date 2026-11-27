/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  buildAgencyRequestCopy,
  isGeneralTaxpayerVatCode,
  shouldShowOpeningBalanceRow,
} from '@/utils/openingView'

describe('openingView helpers', () => {
  it('identifies general taxpayer VAT sub-accounts separately from small-scale VAT account', () => {
    expect(isGeneralTaxpayerVatCode('222100101')).toBe(true)
    expect(isGeneralTaxpayerVatCode('222100110')).toBe(true)
    expect(isGeneralTaxpayerVatCode('2221019')).toBe(true)
    expect(isGeneralTaxpayerVatCode('2221025')).toBe(true)
    expect(isGeneralTaxpayerVatCode('2221026')).toBe(false)
    expect(isGeneralTaxpayerVatCode('1002')).toBe(false)
  })

  it('filters out rare and general-taxpayer VAT accounts in common scope while preserving filled rows', () => {
    const commonCash = { account_code: '1002', account_name: '银行存款', debit_balance: 0, credit_balance: 0 }
    const rareBio = { account_code: '1421', account_name: '消耗性生物资产', debit_balance: 0, credit_balance: 0 }
    const filledBio = { account_code: '1421', account_name: '消耗性生物资产', debit_balance: 1200, credit_balance: 0 }
    const partnerBio = { account_code: '122199', account_name: '其他应收款-待收款项', partner_name: '张三', debit_balance: 0, credit_balance: 0 }
    const genVat = { account_code: '222100101', account_name: '进项税额', debit_balance: 0, credit_balance: 0 }

    expect(shouldShowOpeningBalanceRow(commonCash, { scope: 'common' })).toBe(true)
    expect(shouldShowOpeningBalanceRow(rareBio, { scope: 'common' })).toBe(false)
    expect(shouldShowOpeningBalanceRow(genVat, { scope: 'common', smallScaleTaxpayer: true })).toBe(false)
    expect(shouldShowOpeningBalanceRow(filledBio, { scope: 'common' })).toBe(true)
    expect(shouldShowOpeningBalanceRow(partnerBio, { scope: 'common' })).toBe(true)
    // 搜索全量透出冷门科目
    expect(shouldShowOpeningBalanceRow(rareBio, { scope: 'common', query: '生物' })).toBe(true)
    // 全部模式下全部可见
    expect(shouldShowOpeningBalanceRow(rareBio, { scope: 'all' })).toBe(true)
    expect(shouldShowOpeningBalanceRow(genVat, { scope: 'all' })).toBe(true)
  })

  it('builds natural agency request copy for check and handover scenes without blunt jargon', () => {
    const checkCopy = buildAgencyRequestCopy('2026-01 ～ 2026-09', '杭州惠保信网络科技有限公司')
    expect(checkCopy).toContain('杭州惠保信网络科技有限公司')
    expect(checkCopy).toContain('2026-01 ～ 2026-09')
    expect(checkCopy).toContain('科目余额表')
    expect(checkCopy).toContain('明细')
    expect(checkCopy).not.toContain('用于接账')

    const handoverCopy = buildAgencyRequestCopy('2026-01 ～ 2026-09', '杭州惠保信网络科技有限公司', 'handover')
    expect(handoverCopy).toContain('亲戚')
    expect(handoverCopy).toContain('杭州惠保信网络科技有限公司')
    expect(handoverCopy).toContain('2026-01 ～ 2026-09')
    expect(handoverCopy).toContain('科目余额表')
    expect(handoverCopy).toContain('电子税务局')
  })
})
