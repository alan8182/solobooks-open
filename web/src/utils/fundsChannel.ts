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
  if (normalized === 'receivable' || normalized === 'accounts_receivable') return '应收挂账（待对账）'
  if (normalized.startsWith('employee_personal')) return '员工垫付'
  if (normalized === 'boss_personal') return '股东个人'
  const channel = fundsChannelFromVoucher(voucherLines)
  if (channel === 'wechat_settlement') return '微信商户账户（待结算）'
  if (channel === 'company_bank') return '公司账户（银行）'
  return '公司账户'
}

// ---------------------------------------------------------------------------
// 付款方三值：AI 记账确认卡片的选择器与后端 payer 的互转
// 后端标准值 company_account / boss_personal / employee_personal:<姓名> / receivable，
// 其中 employee_personal 可无姓名（等用户在卡片里选人）。
// ---------------------------------------------------------------------------

export type PayerKind = 'company_account' | 'boss_personal' | 'employee_personal' | 'receivable'

const EMPLOYEE_PAYER_PREFIX = 'employee_personal'

/** 确认卡片里的付款方选项；老板固定一项，不绑员工档案。 */
export const PAYER_OPTIONS: Array<{ label: string; value: PayerKind }> = [
  { label: '公司对公', value: 'company_account' },
  { label: '老板个人垫付', value: 'boss_personal' },
  { label: '员工个人垫付', value: 'employee_personal' },
]

/** 收入类业务的结算方式选项（支持待到账挂应收） */
export const INCOME_PAYER_OPTIONS: Array<{ label: string; value: PayerKind }> = [
  { label: '公司对公（已到账）', value: 'company_account' },
  { label: '待到账（挂应收账款/待流水对账）', value: 'receivable' },
  { label: '老板个人代收', value: 'boss_personal' },
]

/** payer 字符串 → 付款方类型 + 垫付人姓名。历史值 personal 视同老板垫付。 */
export function splitPayer(payer?: unknown): { kind: PayerKind; employeeName: string } {
  const raw = String(payer || '').trim()
  if (raw === 'receivable' || raw === 'accounts_receivable' || raw === '待到账' || raw === '挂应收') {
    return { kind: 'receivable', employeeName: '' }
  }
  if (raw === EMPLOYEE_PAYER_PREFIX || raw.startsWith(`${EMPLOYEE_PAYER_PREFIX}:`)) {
    const sep = raw.indexOf(':')
    const name = sep >= 0 ? raw.slice(sep + 1).trim() : ''
    return { kind: 'employee_personal', employeeName: name }
  }
  if (raw === 'boss_personal' || raw === 'personal') {
    return { kind: 'boss_personal', employeeName: '' }
  }
  return { kind: 'company_account', employeeName: '' }
}

/** 付款方类型 + 姓名 → 后端 payer。员工垫付选人前保持无姓名的 employee_personal。 */
export function joinPayer(kind: PayerKind, employeeName?: string): string {
  if (kind === 'receivable') return 'receivable'
  if (kind === 'employee_personal') {
    const name = String(employeeName || '').trim()
    return name ? `${EMPLOYEE_PAYER_PREFIX}:${name}` : EMPLOYEE_PAYER_PREFIX
  }
  return kind === 'boss_personal' ? 'boss_personal' : 'company_account'
}

/** 卡片上的付款方文字（带垫付人姓名），用于已入账后的只读展示。 */
export function payerCardLabel(payer?: unknown): string {
  const { kind, employeeName } = splitPayer(payer)
  if (kind === 'receivable') return '待到账（挂应收账款）'
  if (kind === 'boss_personal') return '老板个人垫付'
  if (kind === 'employee_personal') return employeeName ? `员工垫付 · ${employeeName}` : '员工垫付'
  return '公司对公'
}
