/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 税费种认定 · 纯函数（粘贴解析 / 展示格式化）。
 *
 * 为什么放这里：税局页面复制出来的是 TSV，列序固定但「序号」列可能缺失、
 * 单元格里可能带换行残留。这段解析是整条导入链路的唯一入口，必须能被单测锁住
 * ——在组件里靠手点验证黏贴，改一次列序就静默错位，错的数据会直接进征期。
 */

import type { FilingCycle, TaxRegistrationRow } from '@/api/tax-registration'

/** 税局「税费种认定信息」表格的列序（不含「序号」） */
export const TAX_REGISTRATION_COLUMNS = [
  '征收项目',
  '征收品目',
  '征收子目',
  '纳税期限',
  '申报期限',
  '征收率',
  '税率或单位税额',
  '主附税标志',
  '征收代理方式',
] as const

export type ParsedPasteRow = Record<string, string>

export interface PasteParseResult {
  rows: ParsedPasteRow[]
  skipped: number
}

const HEADER_HINTS = ['征收项目', '征收品目', '序号']
const SUMMARY_HINTS = ['合计', '小计', '总计']

/**
 * 解析从电子税务局复制出来的表格。
 *
 * - 有制表符按制表符切；从 PDF 复制的用 2 个以上空格切；
 * - 首列是纯数字就当「序号」列吃掉，不是就视为「征收项目」；
 * - 表头行、合计行、列数不足 5 的行计入 skipped，不静默错位。
 */
export function parsePastedTable(text: string): PasteParseResult {
  const rows: ParsedPasteRow[] = []
  let skipped = 0
  for (const rawLine of (text || '').split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line) continue
    const cells = (line.includes('\t') ? line.split('\t') : line.split(/\s{2,}/)).map((cell) =>
      cell.replace(/\s+/g, ' ').trim(),
    )
    if (cells.length < 2) {
      skipped += 1
      continue
    }
    const first = cells[0] || ''
    if (HEADER_HINTS.some((hint) => first.includes(hint))) {
      skipped += 1
      continue
    }
    if (SUMMARY_HINTS.some((hint) => first.startsWith(hint))) {
      skipped += 1
      continue
    }
    const hasSeq = /^\d+$/.test(first)
    const body = hasSeq ? cells.slice(1) : cells
    if (body.length < 5 || !body[0]) {
      skipped += 1
      continue
    }
    // 多空格切分时「空的征收子目」会被吃掉，导致「季」落到子目列。用纳税期限的
    // 位置反推缺了几列：它在第 4 列（下标 3），提前出现就往前补齐空单元格。
    const cycleIndex = body.findIndex((cell) => /^(月|季|年|次|月度|季度|年度)$/.test(cell))
    if (cycleIndex >= 0 && cycleIndex < 3) {
      for (let index = cycleIndex; index < 3; index += 1) body.splice(2, 0, '')
    }
    const row: ParsedPasteRow = { 序号: hasSeq ? first : '' }
    TAX_REGISTRATION_COLUMNS.forEach((column, index) => {
      row[column] = body[index] ?? ''
    })
    rows.push(row)
  }
  return { rows, skipped }
}

/** 百分比展示：0.00025 → 0.025%，0.03 → 3%；空值显示破折号。 */
export function formatRate(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const percent = Number((value * 100).toFixed(8))
  return `${Number(percent.toFixed(6))}%`
}

/** 税率列的展示：带单位（如「8元/平方米」）时原样展示单位文本。 */
export function formatTaxRate(row: Pick<TaxRegistrationRow, 'taxRate' | 'rateUnit'>): string {
  if (row.rateUnit) return row.rateUnit
  return formatRate(row.taxRate)
}

export const CYCLE_TEXT: Record<FilingCycle, string> = {
  month: '月',
  quarter: '季',
  year: '年',
  other: '按次',
}

export function cycleLabel(cycle: FilingCycle): string {
  return CYCLE_TEXT[cycle] ?? cycle
}

/** 属期展示：2026-Q3 → 2026 Q3；2026-09 → 2026年9月；2026 → 2026年度。 */
export function periodLabel(period: string): string {
  const quarter = /^(\d{4})-Q([1-4])$/.exec(period)
  if (quarter) return `${quarter[1]} Q${quarter[2]}`
  const month = /^(\d{4})-(\d{2})$/.exec(period)
  if (month) return `${month[1]}年${Number(month[2])}月`
  if (/^\d{4}$/.test(period)) return `${period}年度`
  return period
}

/** 截止月展示：2026-04 → 2026年4月。 */
export function monthLabel(month: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(month)
  if (!match) return month
  return `${match[1]}年${Number(match[2])}月`
}

export const DEADLINE_STATUS_TEXT: Record<string, string> = {
  pending: '待申报',
  filed: '已申报',
  waived: '不适用',
  not_materialized: '未生成待办',
  untracked: '未生成待办',
}

export function deadlineStatusText(status: string): string {
  return DEADLINE_STATUS_TEXT[status] ?? status
}

export type TagType = 'default' | 'primary' | 'success' | 'warning' | 'error'

export function deadlineStatusType(status: string): TagType {
  if (status === 'filed') return 'success'
  if (status === 'waived') return 'default'
  if (status === 'pending') return 'warning'
  return 'default'
}

/** 导入结果的一句话摘要（消息提示用）。 */
export function summarizeImport(result: {
  created: number
  updated: number
  deactivated: number
}): string {
  const parts = [`新增 ${result.created} 行`, `更新 ${result.updated} 行`]
  if (result.deactivated) parts.push(`停用 ${result.deactivated} 行`)
  return parts.join('，')
}

/** 认定行按申报表组归类，页面上做「一张申报表覆盖哪些税目」的说明。 */
export function groupRowsByReturnGroup(rows: TaxRegistrationRow[]): Array<{
  returnGroup: string
  label: string
  rows: TaxRegistrationRow[]
}> {
  const buckets = new Map<string, { returnGroup: string; label: string; rows: TaxRegistrationRow[] }>()
  for (const row of rows) {
    const bucket = buckets.get(row.returnGroup) ?? {
      returnGroup: row.returnGroup,
      label: row.returnGroupLabel,
      rows: [],
    }
    bucket.rows.push(row)
    buckets.set(row.returnGroup, bucket)
  }
  return Array.from(buckets.values())
}
