/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 *
 * 账簿印刷体格式化。
 *
 * 与 `utils/format.ts` 的 `formatMoney` 是两套口径，不能混用：
 * - `formatMoney` 给普通页面用，带 `¥` 前缀、负数带负号；
 * - 账簿里货币符号只在表头声明「单位：元」，单元格一律纯数字；
 *   余额也不写负号，而是拆成「金额 + 借或贷」两栏（三栏式账页规范）。
 *
 * 金额一律经 `quantizeAmount` 量化后再格式化：后端 `money_text` 输出两位小数，
 * 但前端做合计时会引入 `0.1 + 0.2` 噪声，直接打印会出现多余小数位。
 */
import { quantizeAmount } from './aggregation'

export type BalanceDirection = 'debit' | 'credit' | 'flat'

const THOUSANDS = new Intl.NumberFormat('zh-CN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

/** 账簿金额：纯数字千分位，无货币符号。 */
export function ledgerMoney(value: number | string | null | undefined): string {
  const numeric = Number(value ?? 0)
  if (!Number.isFinite(numeric)) return '0.00'
  return THOUSANDS.format(quantizeAmount(numeric))
}

/** 明细账借贷列：0 留空（账簿惯例，写 0.00 会糊住整页）。 */
export function ledgerAmountCell(value: number | string | null | undefined): string {
  const numeric = Number(value ?? 0)
  if (!Number.isFinite(numeric) || quantizeAmount(numeric) === 0) return ''
  return THOUSANDS.format(quantizeAmount(numeric))
}

/** 余额类单元格：0 用破折号占位，避免误读成「余额为零」。 */
export function ledgerBalanceOrDash(value: number | string | null | undefined): string {
  const numeric = Number(value ?? 0)
  if (!Number.isFinite(numeric) || quantizeAmount(numeric) === 0) return '—'
  return THOUSANDS.format(Math.abs(quantizeAmount(numeric)))
}

/** 借 / 贷 / 平 */
export function directionLabel(direction: string | null | undefined): string {
  if (direction === 'debit') return '借'
  if (direction === 'credit') return '贷'
  return '平'
}

/** 数量：去掉尾随零（后端给 "12.0000"）。 */
export function quantityText(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return ''
  const text = String(value).trim()
  if (!text) return ''
  return text.includes('.') ? text.replace(/0+$/, '').replace(/\.$/, '') : text
}

/** 开区间格式化：YYYY-MM → YYYY年MM月 */
export function monthLabel(month: string | null | undefined): string {
  if (!month) return ''
  const [year, rest] = month.split('-')
  return rest ? `${year}年${rest}月` : String(month)
}
