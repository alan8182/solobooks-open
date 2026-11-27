/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 科目余额表原表快照的共享类型。
 *
 * 导入预览与期初工作台的「科目余额表」页签共用同一份结构：预览行由解析器
 * 直接产出，保存时原样写入期初凭证 metadata；只做核对展示，不参与任何取数。
 */

export type TrialBalanceRowStatus =
  | 'matched'
  | 'unmatched'
  | 'skipped_total'
  | 'skipped_rollup'
  | 'skipped_zero'
  | 'skipped_partner_rollup'

export interface TrialBalanceRow {
  row_no: number
  original_code: string
  original_name: string
  partner_name: string
  /** null = 原表没有这一列/无法判断，不能当成 0 */
  year_start_debit: number | null
  year_start_credit: number | null
  ytd_debit: number | null
  ytd_credit: number | null
  ending_debit: number | null
  ending_credit: number | null
  target_code: string
  target_name: string
  status: TrialBalanceRowStatus
  note: string
}

/** 解析器实际命中的表头，用来告诉用户「我认成了哪几列」 */
export interface TrialBalanceColumns {
  code?: string
  name?: string
  partner?: string
  yearStartDebit?: string
  yearStartCredit?: string
  ytdDebit?: string
  ytdCredit?: string
  endingDebit?: string
  endingCredit?: string
  balance?: string
  direction?: string
}

export const TRIAL_BALANCE_STATUS: Record<
  TrialBalanceRowStatus,
  { label: string; tone: 'success' | 'warning' | 'muted' | 'info' }
> = {
  matched: { label: '已入账', tone: 'success' },
  unmatched: { label: '待映射', tone: 'warning' },
  skipped_total: { label: '合计行', tone: 'muted' },
  skipped_rollup: { label: '层级去重', tone: 'muted' },
  skipped_zero: { label: '零余额', tone: 'muted' },
  skipped_partner_rollup: { label: '客商明细承载', tone: 'info' },
}

/** 后端 metadata 里的金额是字符串；统一转成 number|null，展示层不做二次猜测 */
export function normalizeTrialBalanceRows(raw: unknown): TrialBalanceRow[] {
  if (!Array.isArray(raw)) return []
  const num = (v: unknown): number | null => {
    if (v === null || v === undefined || v === '') return null
    const n = Number(v)
    return Number.isFinite(n) ? n : null
  }
  return raw
    .filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object')
    .map((row, idx) => ({
      row_no: Number(row.row_no ?? idx + 1) || idx + 1,
      original_code: String(row.original_code ?? ''),
      original_name: String(row.original_name ?? ''),
      partner_name: String(row.partner_name ?? ''),
      year_start_debit: num(row.year_start_debit),
      year_start_credit: num(row.year_start_credit),
      ytd_debit: num(row.ytd_debit),
      ytd_credit: num(row.ytd_credit),
      ending_debit: num(row.ending_debit),
      ending_credit: num(row.ending_credit),
      target_code: String(row.target_code ?? ''),
      target_name: String(row.target_name ?? ''),
      status: (row.status as TrialBalanceRowStatus) || 'unmatched',
      note: String(row.note ?? ''),
    }))
}

export interface TrialBalanceTotals {
  /**
   * 只有原表自带合计行时才出合计。源表没有合计行时不按展示行相加：
   * 上下级重复行会让合计虚增，看起来像「原表不平」，反而误导核对。
   */
  hasTotalRow: boolean
  debit: { yearStart: number | null; ytd: number | null; ending: number | null }
  credit: { yearStart: number | null; ytd: number | null; ending: number | null }
}

type AmountKey =
  | 'year_start_debit'
  | 'year_start_credit'
  | 'ytd_debit'
  | 'ytd_credit'
  | 'ending_debit'
  | 'ending_credit'

function sumColumn(rows: TrialBalanceRow[], key: AmountKey): number | null {
  const values = rows
    .map(row => row[key])
    .filter((value): value is number => value !== null)
  // 整组列都不存在才是「—」；部分行没有（如损益行不适用年初列）时只加有值的行
  if (values.length === 0) return null
  return Math.round(values.reduce((sum, value) => sum + value, 0) * 100) / 100
}

export function trialBalanceTotals(rows: TrialBalanceRow[]): TrialBalanceTotals {
  const totalRows = rows.filter(row => row.status === 'skipped_total')
  return {
    hasTotalRow: totalRows.length > 0,
    debit: {
      yearStart: sumColumn(totalRows, 'year_start_debit'),
      ytd: sumColumn(totalRows, 'ytd_debit'),
      ending: sumColumn(totalRows, 'ending_debit'),
    },
    credit: {
      yearStart: sumColumn(totalRows, 'year_start_credit'),
      ytd: sumColumn(totalRows, 'ytd_credit'),
      ending: sumColumn(totalRows, 'ending_credit'),
    },
  }
}

export type TrialBalanceStatusFilter = 'all' | 'matched' | 'unmatched' | 'rollup' | 'zero'

export function isTrialBalanceZeroRow(row: TrialBalanceRow): boolean {
  return row.status === 'skipped_zero'
}

/**
 * 会计科目编码层级（用于原表缩进与一级汇总加粗）：
 * - 0: 一级（<= 4 位，如 1002、2221）
 * - 1: 二级（5~7 位，如 1122001、2211001）
 * - 2: 三级及以上（> 7 位，如 2221001005）
 */
export function trialBalanceAccountDepth(code: string): 0 | 1 | 2 {
  const cleaned = String(code || '').trim().replace(/[.\-_]/g, '')
  if (!cleaned || cleaned.length <= 4) return 0
  if (cleaned.length <= 7) return 1
  return 2
}

export interface TrialBalanceFilterOptions {
  statusFilter?: TrialBalanceStatusFilter
  hideZero?: boolean
  query?: string
}

export function filterTrialBalanceRows(
  rows: TrialBalanceRow[],
  opts: TrialBalanceFilterOptions = {},
): TrialBalanceRow[] {
  const statusFilter = opts.statusFilter || 'all'
  const query = String(opts.query || '').trim().toLowerCase()

  return rows.filter((row) => {
    if (statusFilter === 'matched' && row.status !== 'matched') return false
    if (statusFilter === 'unmatched' && row.status !== 'unmatched') return false
    if (
      statusFilter === 'rollup'
      && row.status !== 'skipped_rollup'
      && row.status !== 'skipped_partner_rollup'
    ) {
      return false
    }
    if (statusFilter === 'zero' && !isTrialBalanceZeroRow(row)) return false

    if (query) {
      const haystacks = [
        row.original_code,
        row.original_name,
        row.partner_name,
        row.target_code,
        row.target_name,
        row.note,
      ]
      return haystacks.some(v => String(v || '').toLowerCase().includes(query))
    }

    if (opts.hideZero && statusFilter !== 'zero' && isTrialBalanceZeroRow(row)) {
      return false
    }
    return true
  })
}
