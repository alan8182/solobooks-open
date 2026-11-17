/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import type { ExpectedPeriod } from './openingBalanceImport'

export interface InceptionPeriod {
  year: number
  month: number
}

/** 公司成立年月；没有或格式非法时返回 null */
export function companyInception(companyStartDate: string | null | undefined): InceptionPeriod | null {
  const raw = String(companyStartDate || '')
  const year = Number(raw.slice(0, 4))
  const month = Number(raw.slice(5, 7))
  if (!Number.isInteger(year) || year < 1900 || year > 2100) return null
  if (!Number.isInteger(month) || month < 1 || month > 12) return null
  return { year, month }
}

/**
 * 期初建账的默认启用期间。
 *
 * 优先级：该会计年度已保存的 start_period > 公司信息里的启用日期（仅建账当年）
 * > 年初。以前没保存过时一律落到 1 月，公司明明是 10 月启用的，页面却显示
 * 「1 月启用」，用户得自己发现并手改——建账时间本来就应该跟启用日期一致。
 */
/** 期初导入「截止月」兜底下拉的选项：只列能通过期间校验的月份 */
export function openingCutoffOptions(expected: ExpectedPeriod): string[] {
  if (expected.zeroOpening) return []
  const start = expected.startYear * 12 + expected.startMonth
  const end = expected.endYear * 12 + expected.endMonth
  if (end < start) return []
  return Array.from({ length: end - start + 1 }, (_, index) => {
    const value = start + index
    const year = Math.floor((value - 1) / 12)
    const month = ((value - 1) % 12) + 1
    return `${year}-${String(month).padStart(2, '0')}`
  })
}

export function resolveOpeningPeriod(
  savedStartPeriod: unknown,
  companyStartDate: string | null | undefined,
  fiscalYear?: number,
): { year: number | null; month: string } {
  const saved = typeof savedStartPeriod === 'string' ? savedStartPeriod : ''
  const savedYear = saved.slice(0, 4)
  const savedMonth = saved.slice(5, 7)
  const yearMatches = !fiscalYear || Number(savedYear) === fiscalYear
  if (/^\d{4}$/.test(savedYear) && /^\d{2}$/.test(savedMonth) && yearMatches) {
    return { year: Number(savedYear), month: savedMonth }
  }

  const raw = String(companyStartDate || '')
  const year = Number(raw.slice(0, 4))
  const month = raw.slice(5, 7)
  const validYear = Number.isFinite(year) && year > 0 ? year : null
  // 公司启用日期只管建账那一年；其他年度的期初是结转，默认年初
  if (validYear && (!fiscalYear || validYear === fiscalYear) && /^\d{2}$/.test(month)) {
    return { year: validYear, month }
  }
  return { year: validYear, month: '01' }
}
