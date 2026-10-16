/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export interface VoucherLineLike {
  accountCode?: string | number | null
  amount?: string | number | null
  [key: string]: unknown
}

export interface VoucherLinesLike {
  debit?: VoucherLineLike[] | null
  credit?: VoucherLineLike[] | null
}

const WECHAT_CLEARING_CODES = ['1012001', '1012'] // 现行（其他货币资金二级）/ 历史
const BANK_CODE = '1002'

function sideEffect(lines: VoucherLineLike[] | null | undefined, codes: string[]): number {
  return (lines || []).reduce(
    (sum, line) => (codes.includes(String(line.accountCode || '')) ? sum + (Number((line as any).amount) || 0) : sum),
    0,
  )
}

/**
 * 从凭证分录推断资金渠道：微信待结算清算科目（1012001/历史 1012）净变动非零即微信商户账户，
 * 其次看银行存款（1002）；两者皆无（如垫付核销）返回空串。
 */
export function fundsChannelFromVoucher(voucherLines?: VoucherLinesLike | null): 'wechat_settlement' | 'company_bank' | '' {
  if (!voucherLines || typeof voucherLines !== 'object') return ''
  const wechatNet = sideEffect(voucherLines.debit, WECHAT_CLEARING_CODES) - sideEffect(voucherLines.credit, WECHAT_CLEARING_CODES)
  if (Math.abs(wechatNet) > 1e-9) return 'wechat_settlement'
  const bankNet = sideEffect(voucherLines.debit, [BANK_CODE]) - sideEffect(voucherLines.credit, [BANK_CODE])
  if (Math.abs(bankNet) > 1e-9) return 'company_bank'
  return ''
}

/**
 * 付款方式展示：payer 表达资金归属（公司/股东/员工），资金渠道由凭证科目
 * 决定——微信待结算收支显示"微信商户账户（待结算）"，避免误读为公司银行账户。
 */
export function payerDisplayLabel(
  payer?: string | null,
  voucherLines?: VoucherLinesLike | null,
): string {
  const normalized = String(payer || '')
  if (normalized.startsWith('employee_personal')) return '员工垫付'
  if (normalized === 'boss_personal') return '股东个人'
  const channel = fundsChannelFromVoucher(voucherLines)
  if (channel === 'wechat_settlement') return '微信商户账户（待结算）'
  if (channel === 'company_bank') return '公司账户（银行）'
  return '公司账户'
}
