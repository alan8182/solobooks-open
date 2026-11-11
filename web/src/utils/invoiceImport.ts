/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { joinPayer, splitPayer, type PayerKind } from './fundsChannel'

/** 发票导入确认卡片的付款方选项：默认公司账户，只有用户选垫付才改凭证对手方。 */
export const INVOICE_PAYER_OPTIONS: Array<{ label: string; value: PayerKind }> = [
  { label: '公司账户', value: 'company_account' },
  { label: '老板垫付', value: 'boss_personal' },
  { label: '员工垫付', value: 'employee_personal' },
]

export interface InvoicePayerState {
  kind: PayerKind
  employeeName: string
}

/** 行上初始付款方：沿用后端给的 payer，缺省公司账户（与导入前行为一致）。 */
export function initialInvoicePayer(payer?: unknown): InvoicePayerState {
  const { kind, employeeName } = splitPayer(payer)
  return { kind: kind === 'receivable' ? 'company_account' : kind, employeeName }
}

/**
 * 行上选择的付款方 → 后端 payer。
 * 收入票的结算方式仍由科目（1122 应收）决定，这里固定回公司账户，
 * 避免「老板垫付」把销项票的应收账款对手方抢走。
 */
export function invoiceRowPayer(
  direction: string,
  payer?: InvoicePayerState | null,
): string {
  if (direction !== 'expense') return 'company_account'
  return joinPayer(payer?.kind || 'company_account', payer?.employeeName)
}

/** 员工垫付必须选到人：裸 employee_personal 不会建个人往来，2241 会两头落空。 */
export function hasUnnamedEmployeePayer(
  rows: Array<{ direction: string; payer?: InvoicePayerState | null }>,
): boolean {
  return rows.some(
    (row) =>
      row.direction === 'expense' &&
      row.payer?.kind === 'employee_personal' &&
      !String(row.payer.employeeName || '').trim(),
  )
}
