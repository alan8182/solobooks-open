/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 期初建账的默认启用期间。
 *
 * 优先级：该会计年度已保存的 start_period > 公司信息里的启用日期（仅建账当年）
 * > 年初。以前没保存过时一律落到 1 月，公司明明是 10 月启用的，页面却显示
 * 「1 月启用」，用户得自己发现并手改——建账时间本来就应该跟启用日期一致。
 */
/** 期初导入「截止月」兜底下拉的选项：建账年 1 月 ~ 启动月（年初建账为上年全年） */
export function openingCutoffOptions(fiscalYear: number, startMonth: string): string[] {
  const month = parseInt(startMonth, 10) || 1
  const year = month > 1 ? fiscalYear : fiscalYear - 1
  const endMonth = month > 1 ? month : 12
  return Array.from(
    { length: endMonth },
    (_, index) => `${year}-${String(index + 1).padStart(2, '0')}`,
  )
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
